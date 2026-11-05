import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SENSITIVE_KEYS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "api-key",
    "apikey",
    "access-key",
    "private_key",
    "csrf",
    "xsrf",
    "signature",
)
TOKEN_PATTERN = re.compile(r"AIza[\w-]{35}|sk-[\w-]{20,}|(?i:Bearer\s+)[\w.+/=-]{12,}")


def _is_sensitive(key):
    folded = key.casefold()
    return folded in {"code", "key"} or any(part in folded for part in SENSITIVE_KEYS)


def redact(value, key="", depth=0):
    if _is_sensitive(key):
        return "[redacted]"
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        redacted = {
            str(name)[:100]: redact(item, str(name), depth + 1)
            for name, item in list(value.items())[:40]
        }
        if len(value) > 40:
            redacted["_truncated_fields"] = len(value) - 40
        return redacted
    if isinstance(value, list):
        items = [redact(item, depth=depth + 1) for item in value[:100]]
        if len(value) <= 100:
            return items
        return {"items": items, "truncated": len(value) - 100}
    if isinstance(value, str):
        return TOKEN_PATTERN.sub("[redacted]", value)[:4096]
    return value


async def request_details(request, message, session_id, locked_ids):
    details = {
        "client_ip": None,
        "submitted_message": message,
        "session_id": session_id,
        "body_source": "handler_payload",
        "body": {"message": message, "locked_ids": locked_ids},
    }
    if request is None:
        return redact(details)

    client = request.client
    details.update(
        {
            "client_ip": client.host if client else None,
            "client_port": client.port if client else None,
            "headers": redact(dict(request.headers)),
            "query": redact(dict(request.query_params)),
            "gradio_session": request.session_hash,
        }
    )
    try:
        url = urlsplit(str(request.url))
    except ValueError:
        details["url_invalid"] = True
    else:
        details["url"] = {
            "scheme": url.scheme,
            "host": url.hostname,
            "path": url.path,
        }

    native = request.request
    if native is not None:
        details.update(
            {
                "method": native.method,
                "http_version": native.scope.get("http_version"),
                "server": native.scope.get("server"),
                "path_params": dict(native.path_params),
            }
        )
        try:
            body = await native.body()
        except RuntimeError:
            details["body_unavailable"] = True
        else:
            details["body_size_bytes"] = len(body)
            details["body_sha256"] = hashlib.sha256(body).hexdigest()
            if len(body) <= 16384:
                try:
                    details["body"] = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    details["body"] = body.decode("utf-8", errors="replace")
                details["body_source"] = "http_request"
            else:
                details["body_truncated"] = True
    return redact(details)


def log_rejection(path, request, moderation, model):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event_id = str(uuid.uuid4())
    payload = redact({"request": request, "moderation": moderation, "model": model})

    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 65536:
        metadata = payload["request"]
        metadata["body"] = {"omitted_for_size": True}
        for field in ("headers", "query"):
            metadata[field] = {
                key: value[:256]
                for key, value in list(metadata.get(field, {}).items())[:20]
            }
        payload["metadata_truncated"] = True

    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events "
                "(event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                "category TEXT NOT NULL, details TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    moderation["category"],
                    json.dumps(payload, ensure_ascii=False, allow_nan=False),
                ),
            )
            connection.execute(
                "DELETE FROM events WHERE rowid NOT IN "
                "(SELECT rowid FROM events ORDER BY rowid DESC LIMIT ?)",
                (1000,),
            )
    return event_id


def rejection_message(client_ip, category):
    if client_ip:
        return (
            f"This request is not allowed ({category}). The incident, request metadata "
            f"and your connection IP address ({client_ip}) have been logged."
        )
    return (
        f"This request is not allowed ({category}). The incident and request metadata "
        "have been logged; a client IP address was not available."
    )
