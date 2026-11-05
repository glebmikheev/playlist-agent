import asyncio
import csv
import hashlib
import io
import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from google.api_core.exceptions import GoogleAPICallError, RetryError
from pydantic import BaseModel, ConfigDict, Field

from playlist_agent.agent import run_agent
from playlist_agent.catalogue import Catalogue
from playlist_agent.models import PlaylistSpec
from playlist_agent.presentation import describe_spec, profile_svg, table_rows
from playlist_agent.security import rejection_message
from playlist_agent.storage import update_snapshot

from .config import CloudSettings
from .model import BudgetedGemini
from .policy import EXPLANATION, Denied
from .security import (
    TrafficGuard,
    audit,
    client_ip,
    cookie_name,
    cookie_owner,
    ip_key,
    new_cookie,
)
from .store import FirestoreStore

ASSETS = Path(__file__).parent / "assets"
STORE_ERRORS = (GoogleAPICallError, RetryError)
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    session_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=2000)
    locked_ids: list[str] = Field(default_factory=list, max_length=100)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)

        chunks = []
        size = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            size += len(chunk)
            if size > 16384:
                return await JSONResponse(
                    {"message": "The request is too large."}, status_code=413
                )(scope, receive, send)
            chunks.append(chunk)
            if not event.get("more_body", False):
                break

        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body}
            return await receive()

        await self.app(scope, replay, send)


def compact_snapshot(snapshot):
    snapshot["messages"] = snapshot.get("messages", [])[-12:]
    snapshot["context_messages"] = snapshot.get("context_messages", [])[-6:]
    attempt = snapshot.get("last_attempt", {})
    attempt.pop("spec", None)
    attempt.pop("playlist_ids", None)

    for field in ("messages", "context_messages"):
        while (
            len(json.dumps(snapshot, ensure_ascii=False).encode()) > 55 * 1024
            and len(snapshot[field]) > 2
        ):
            snapshot[field] = snapshot[field][2:]
    return snapshot


def public_result(session, catalogue):
    snapshot = session["snapshot"]
    data = snapshot.get("spec") or snapshot.get("draft_spec")
    spec = PlaylistSpec.model_validate(data) if data else None
    tracks = catalogue.tracks(snapshot.get("playlist_ids", []))
    attempt = snapshot.get("last_attempt", {})
    return {
        "id": snapshot["session_id"],
        "expires": session["expires"],
        "messages": snapshot.get("messages", []),
        "status": attempt.get("status", "new"),
        "message": attempt.get(
            "message",
            "Describe the duration or track count and your musical preferences.",
        ),
        "interpretation": describe_spec(spec).replace("**", "") if spec else "",
        "chart": profile_svg(spec, tracks) if spec else "",
        "tracks": [
            {
                "id": track["track_id"],
                "title": track["title"],
                "artists": track["artists"],
                "minutes": round(track["duration_ms"] / 60000, 2),
                "energy": track["energy"],
                "genres": track["genres"],
            }
            for track in tracks
        ],
        "locked_ids": spec.locked_track_ids if spec else [],
        "metrics": {
            key: attempt.get("metrics", {}).get(key)
            for key in ("elapsed_s", "total_tokens", "model_operations")
        },
    }


def create_app(config=None, store=None, model_factory=BudgetedGemini, clock=time.time):
    config = config if config is not None else CloudSettings.load()
    store = store if store is not None else FirestoreStore(config)
    catalogue = Catalogue(Path(__file__).resolve().parents[1] / "data/catalogue.sqlite")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard = TrafficGuard()

    @app.exception_handler(Denied)
    async def denied_handler(request, error):
        headers = None
        if error.next_at:
            retry_after = str(max(1, int(error.next_at - clock())))
            headers = {"Retry-After": retry_after}
        return JSONResponse(
            {"message": str(error), "reason": error.reason, "next_at": error.next_at},
            status_code=error.status,
            headers=headers,
        )

    @app.exception_handler(PermissionError)
    async def owner_handler(request, error):
        return JSONResponse(
            {
                "message": "This conversation is unavailable in this browser. Please start a new conversation."
            },
            status_code=404,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        return JSONResponse(
            {
                "message": "Check the request format. Enter 1–2,000 characters and valid conversation/request IDs."
            },
            status_code=422,
        )

    @app.exception_handler(GoogleAPICallError)
    @app.exception_handler(RetryError)
    async def storage_handler(request, error):
        return JSONResponse(
            {
                "message": "Demo storage is temporarily unavailable. Your previous playlist is preserved. Please try again later."
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unexpected_handler(request, error):
        # Log the class, not arbitrary exception text that could contain credentials.
        print(
            json.dumps(
                {
                    "severity": "ERROR",
                    "message": "Cloud demo request failed",
                    "error_type": type(error).__name__,
                }
            ),
            flush=True,
        )
        return JSONResponse(
            {
                "message": "The demo could not complete this request. Please try again later."
            },
            status_code=500,
        )

    @app.middleware("http")
    async def protect(request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        now = clock()
        try:
            address = client_ip(request, config)
        except ValueError:
            return JSONResponse(
                {"message": "The demo could not verify the client network."},
                status_code=400,
            )

        key = ip_key(config, address)
        path = request.url.path
        is_api = path.startswith("/api/")
        try:
            guard.check(key, now, is_api)
        except Denied as error:
            return await denied_handler(request, error)

        request.state.address = address
        request.state.ip = key
        token = request.cookies.get(cookie_name(config))
        request.state.owner = cookie_owner(config, token, now)
        if is_api and request.state.owner is None:
            return JSONResponse(
                {
                    "message": "Your guest session has expired. Reload the page to start a new session."
                },
                status_code=401,
            )

        if (
            is_api
            and request.method == "POST"
            and (
                request.headers.get("origin") != config.origin
                or request.headers.get("x-demo-request") != "1"
            )
        ):
            return JSONResponse(
                {"message": "This request origin is not allowed."}, status_code=403
            )

        if is_api:
            try:
                await asyncio.to_thread(store.charge_http, now)
                await asyncio.to_thread(store.cleanup, now)
            except Denied as error:
                if error.reason == "http" and error.next_at:
                    guard.closed_until = error.next_at
                return await denied_handler(request, error)
            except STORE_ERRORS:
                return await denied_handler(request, Denied("unavailable", status=503))

        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/" and request.state.owner is None:
            response.set_cookie(
                cookie_name(config),
                new_cookie(config, now),
                max_age=config.retention_days * 86400,
                secure=config.secure_cookie,
                httponly=True,
                samesite="lax",
                path="/",
            )
        return response

    app.add_middleware(BodyLimit)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse((ASSETS / "index.html").read_text(encoding="utf-8"))

    @app.get("/assets/app.js")
    async def javascript():
        return Response(
            (ASSETS / "app.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
        )

    @app.get("/assets/style.css")
    async def stylesheet():
        return Response(
            (ASSETS / "style.css").read_text(encoding="utf-8"), media_type="text/css"
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def sessions(request: Request):
        return await asyncio.to_thread(
            store.list_sessions, request.state.owner, clock()
        )

    @app.get("/api/sessions/{session_id}")
    async def session(session_id: uuid.UUID, request: Request):
        record = await asyncio.to_thread(
            store.load_session, request.state.owner, str(session_id), clock()
        )
        return public_result(record, catalogue)

    @app.get("/api/sessions/{session_id}/export/{format}")
    async def export(session_id: uuid.UUID, format: str, request: Request):
        if format not in {"json", "csv"}:
            raise HTTPException(404)
        record = await asyncio.to_thread(
            store.load_session, request.state.owner, str(session_id), clock()
        )
        snapshot = record["snapshot"]
        tracks = catalogue.tracks(snapshot.get("playlist_ids", []))

        if format == "json":
            content = json.dumps(
                {"spec": snapshot.get("spec"), "tracks": tracks},
                ensure_ascii=False,
                allow_nan=False,
            )
            media = "application/json"
        else:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(
                [
                    "position",
                    "title",
                    "artists",
                    "minutes",
                    "energy",
                    "genres",
                    "spotify_url",
                ]
            )
            for row in table_rows(tracks):
                writer.writerow(
                    [
                        "'" + value
                        if isinstance(value, str)
                        and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                        else value
                        for value in row
                    ]
                )
            content = "\ufeff" + output.getvalue()
            media = "text/csv"

        filename = f'attachment; filename="playlist-{session_id}.{format}"'
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": filename},
        )

    @app.post("/api/generate")
    async def generate(payload: GenerateRequest, request: Request):
        owner = request.state.owner
        now = clock()
        if not payload.message.strip():
            raise HTTPException(422, "Enter a musical request.")

        request_id = str(payload.request_id)
        if payload.session_id:
            session_id = str(payload.session_id)
        else:
            session_id = str(uuid.uuid4())

        if payload.session_id:
            previous = await asyncio.to_thread(
                store.load_session, owner, session_id, now
            )
            allowed = set(previous["snapshot"].get("playlist_ids", []))
        else:
            allowed = set()
        if not set(payload.locked_ids) <= allowed:
            raise HTTPException(
                422, "Only tracks in your accepted playlist can be locked."
            )

        fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        try:
            run, fresh = await asyncio.to_thread(
                store.admit,
                owner,
                request.state.ip,
                request_id,
                fingerprint,
                session_id,
                now,
                payload.session_id is None,
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

        session_id = run["session_id"]
        if not fresh and run["status"] == "pending":
            message = (
                "This request is already running. No duplicate model call was made."
            )
            if clock() > run["created"] + config.model.timeout_s + 30:
                message = "The original request was interrupted and its outcome is unknown. Open this conversation before making a new attempt; this request ID will not be replayed."
            return JSONResponse(
                {"message": message, "session_id": session_id},
                status_code=202,
            )

        record = await asyncio.to_thread(store.load_session, owner, session_id, clock())
        if not fresh:
            return public_result(record, catalogue)

        snapshot = record["snapshot"]
        for field in ("spec", "draft_spec"):
            if snapshot.get(field):
                snapshot[field]["locked_track_ids"] = payload.locked_ids

        model = model_factory(config.model, store, owner, request_id)
        try:
            remaining = max(0.01, run["created"] + config.model.timeout_s - clock())
            result = await run_agent(
                payload.message, catalogue, model, snapshot, remaining
            )
        finally:
            await model.close()

        if result["status"] == "rejected":
            try:
                result["audit_event_id"] = audit(
                    request,
                    payload.model_dump(mode="json"),
                    result["moderation"],
                    request.state.address,
                    config.model.model,
                )
            except OSError:
                result["message"] = (
                    "This request is not allowed. Security logging is temporarily unavailable."
                )
            else:
                result["message"] = rejection_message(
                    request.state.address, result["moderation"]["category"]
                )

        if result["status"] == "error":
            if result.get("failure_reason") == "provider_unavailable":
                result["message"] = (
                    "The model is currently unavailable. Please try again later. Your guest allowance was not used."
                )
            elif "quota or rate" in result["message"]:
                result["message"] = (
                    "The AI provider has temporarily limited this free demo. Please try again later. "
                    + EXPLANATION
                )
            elif EXPLANATION not in result["message"]:
                result["message"] = (
                    "The demo could not complete this request. Your previous playlist is preserved. This run did not use your guest allowance. Please try again later."
                )

        snapshot = compact_snapshot(update_snapshot(snapshot, payload.message, result))
        await asyncio.to_thread(
            store.finish, owner, request_id, result, snapshot, clock()
        )
        saved = await asyncio.to_thread(store.load_session, owner, session_id, clock())
        return public_result(saved, catalogue)

    return app
