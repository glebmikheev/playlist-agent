import os
import sqlite3
import uuid
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("FORWARDED_ALLOW_IPS", "")

import gradio as gr

from playlist_agent.agent import run_agent
from playlist_agent.catalogue import Catalogue
from playlist_agent.gemini import GeminiClient, ModelError
from playlist_agent.models import PlaylistSpec
from playlist_agent.presentation import (
    describe_spec,
    export_playlist,
    profile_svg,
    table_rows,
)
from playlist_agent.security import log_rejection, rejection_message, request_details
from playlist_agent.settings import ROOT, Settings
from playlist_agent.storage import SessionStore, update_snapshot


def create_app(
    catalogue_path=ROOT / "data/catalogue.sqlite",
    session_dir=ROOT / "data/sessions",
    audit_path=ROOT / "data/security/audit.sqlite",
):
    catalogue, store = Catalogue(catalogue_path), SessionStore(session_dir)

    def render(snapshot):
        attempt = snapshot.get("last_attempt", {})
        ids = snapshot.get("playlist_ids", [])
        spec_data = snapshot.get("spec")
        if not ids and attempt.get("playlist_ids"):
            ids, spec_data = attempt["playlist_ids"], attempt["spec"]
        tracks = catalogue.tracks(ids)
        spec = PlaylistSpec.model_validate(spec_data) if spec_data else None
        files = (
            export_playlist(ROOT / "data/exports", snapshot["session_id"], spec, tracks)
            if spec and tracks
            else []
        )
        status = attempt.get(
            "message",
            "Describe the duration or track count and your musical preferences.",
        )
        if tracks:
            status += f"\n\n**{len(tracks)} tracks · {sum(track['duration_ms'] for track in tracks) / 60000:.1f} min.**"
        if attempt.get("status") in {
            "failed",
            "error",
            "clarification",
            "rejected",
        } and snapshot.get("playlist_ids"):
            status += "\n\nShowing the last accepted playlist."
        if attempt.get("changes"):
            change = attempt["changes"]
            status += f"\n\nAdded: {change['added']}; removed: {change['removed']}; retained: {change['retained']}."
        details = {
            "status": attempt.get("status"),
            "interpreted_spec": snapshot.get("draft_spec") or spec_data,
            "validation": attempt.get("validation"),
            "metrics": attempt.get("metrics"),
            "moderation": attempt.get("moderation"),
            "audit_event_id": attempt.get("audit_event_id"),
            "usage": snapshot.get("usage"),
        }
        return (
            snapshot.get("messages", []),
            status,
            describe_spec(
                PlaylistSpec.model_validate(snapshot.get("draft_spec") or spec_data)
            )
            if snapshot.get("draft_spec") or spec_data
            else "",
            table_rows(tracks),
            profile_svg(spec, tracks) if spec else "",
            gr.update(
                choices=[
                    (f"{i}. {track['title']} — {track['artists']}", track["track_id"])
                    for i, track in enumerate(tracks, 1)
                ],
                value=spec.locked_track_ids if spec else [],
            ),
            files,
            details,
            snapshot["session_id"],
            gr.update(
                choices=store.list_sessions(),
                value=snapshot["session_id"]
                if (store.directory / f"{snapshot['session_id']}.json").exists()
                else None,
            ),
        )

    async def submit(message, session_id, locked_ids, request: gr.Request):
        if not message.strip() or len(message) > 2000:
            raise gr.Error("Enter a message between 1 and 2,000 characters.")
        snapshot = store.load(session_id or str(uuid.uuid4()))
        if snapshot.get("spec"):
            snapshot["spec"]["locked_track_ids"] = list(locked_ids)
            if snapshot.get("draft_spec"):
                snapshot["draft_spec"]["locked_track_ids"] = list(locked_ids)
        settings = Settings.load()
        try:
            client = GeminiClient(settings)
        except ModelError as exc:
            result = {"status": "error", "message": str(exc)}
        else:
            try:
                result = await run_agent(
                    message, catalogue, client, snapshot, settings.timeout_s
                )
            finally:
                await client.close()
        if result["status"] == "rejected":
            metadata = await request_details(
                request, message, snapshot["session_id"], locked_ids
            )
            try:
                event_id = log_rejection(
                    audit_path, metadata, result["moderation"], settings.model
                )
            except (OSError, sqlite3.Error):
                result["message"] = (
                    "This request is not allowed. Audit storage is unavailable; the request was rejected."
                )
            else:
                result["audit_event_id"] = event_id
                result["message"] = rejection_message(
                    metadata["client_ip"], result["moderation"]["category"]
                )
        store.save(update_snapshot(snapshot, message, result))
        return (*render(snapshot), message if result["status"] == "error" else "")

    def new_session():
        return render(store.load(str(uuid.uuid4())))

    def restore(session_id):
        if not session_id:
            raise gr.Error("Choose a saved conversation.")
        return render(store.load(session_id))

    def save_locks(session_id, selected):
        if not session_id:
            return
        snapshot = store.load(session_id)
        if not set(selected) <= set(snapshot.get("playlist_ids", [])):
            raise gr.Error("Only tracks from the accepted playlist can be locked.")
        if snapshot.get("spec"):
            snapshot["spec"]["locked_track_ids"] = list(selected)
            if snapshot.get("draft_spec"):
                snapshot["draft_spec"]["locked_track_ids"] = list(selected)
            store.save(snapshot)

    with gr.Blocks(title="Playlist Agent", analytics_enabled=False) as app:
        gr.Markdown(
            "# Playlist Agent\nBuild and refine playlists with Gemini, real catalogue tracks and checked constraints."
        )
        gr.Markdown(
            "For abuse prevention, rejected requests are logged with the submitted text, connection IP and redacted HTTP metadata."
        )
        session_id = gr.State("")
        with gr.Row():
            sessions = gr.Dropdown(
                choices=store.list_sessions(), label="Saved conversation", scale=4
            )
            restore_button = gr.Button("Open")
            new_button = gr.Button("New conversation")
        with gr.Row():
            with gr.Column(scale=1):
                chat = gr.Chatbot(label="Conversation", height=330)
                message = gr.Textbox(
                    label="Request",
                    lines=3,
                    placeholder="50 minutes of electronic music for a walk: energetic middle, calm ending...",
                )
                submit_button = gr.Button("Build / refine", variant="primary")
                gr.Examples(
                    [
                        "50 minutes of electronic music for a walk: first 10 minutes calm, then more energetic, final 10 minutes calm. No repeated artists.",
                        "20 instrumental tracks for focused work, preferably ambient and chill. At most two tracks per artist.",
                        "80 electronic tracks: calm opening, energetic middle and calm ending. No repeated artists.",
                    ],
                    inputs=message,
                    cache_examples=False,
                )
                spec_view = gr.Markdown()
            with gr.Column(scale=2):
                status = gr.Markdown()
                plot = gr.HTML()
                table = gr.Dataframe(
                    headers=[
                        "№",
                        "Track",
                        "Artists",
                        "Minutes",
                        "Energy",
                        "Genres",
                        "Spotify",
                    ],
                    datatype=["number", "str", "str", "number", "number", "str", "str"],
                    interactive=False,
                )
                locks = gr.Dropdown(choices=[], multiselect=True, label="Locked tracks")
                files = gr.File(
                    label="Export CSV / JSON", file_count="multiple", interactive=False
                )
                with gr.Accordion("Checks and metrics", open=False):
                    details = gr.JSON()
        outputs = [
            chat,
            status,
            spec_view,
            table,
            plot,
            locks,
            files,
            details,
            session_id,
            sessions,
        ]
        inputs = [message, session_id, locks]
        submit_button.click(
            submit,
            inputs,
            [*outputs, message],
            concurrency_limit=1,
            concurrency_id="agent",
            api_name="generate",
        )
        message.submit(
            submit,
            inputs,
            [*outputs, message],
            concurrency_limit=1,
            concurrency_id="agent",
            api_name=False,
        )
        locks.input(
            save_locks,
            [session_id, locks],
            api_name=False,
            concurrency_id="agent",
            concurrency_limit=1,
        )
        new_button.click(new_session, outputs=outputs, api_name=False)
        restore_button.click(restore, sessions, outputs, api_name="restore")
        core_asset = next(
            (Path(gr.__file__).parent / "templates/frontend/assets").glob("core-*.js")
        ).name
        app.load(
            new_session,
            outputs=outputs,
            api_name=False,
            js='async () => {const {changeLocale} = await import("/assets/'
            + core_asset
            + '"); changeLocale("en"); return [];}',
        )
    return app.queue(max_size=4, default_concurrency_limit=1)


if __name__ == "__main__":
    create_app().launch(
        server_name=os.environ.get("PLAYLIST_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("PORT", "7860")),
        share=False,
        show_error=False,
        enable_monitoring=False,
        footer_links=[],
        blocked_paths=[
            str(ROOT / name)
            for name in (
                ".git",
                "tests",
                "work",
                "reports",
                "data/sessions",
                "data/security",
                "AGENT.md",
            )
        ]
        + [str(path) for path in ROOT.glob(".env*")],
    )
