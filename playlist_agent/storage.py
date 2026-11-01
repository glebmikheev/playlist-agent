import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SessionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(self, session_id):
        session_id = str(uuid.UUID(session_id))
        path = self.directory / f"{session_id}.json"
        if not path.exists():
            return {
                "schema_version": 1,
                "session_id": session_id,
                "messages": [],
                "playlist_ids": [],
            }
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot["schema_version"] != 1:
            raise ValueError("Unsupported session version.")
        return snapshot

    def save(self, snapshot):
        session_id = str(uuid.UUID(snapshot["session_id"]))
        snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.directory, delete=False, suffix=".tmp"
        ) as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.directory / f"{session_id}.json")

    def list_sessions(self):
        choices = []
        for path in sorted(
            self.directory.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            data = json.loads(path.read_text(encoding="utf-8"))
            spec = data.get("spec") or data.get("draft_spec") or {}
            label = (
                data.get("updated_at", "")[:16]
                + " · "
                + spec.get("semantic_brief", "Диалог")[:55]
            )
            choices.append((label, path.stem))
        return choices


def update_snapshot(snapshot, message, result):
    previous = set(snapshot.get("playlist_ids", []))
    snapshot["messages"] = [
        *snapshot.get("messages", []),
        {"role": "user", "content": message},
        {"role": "assistant", "content": result["message"]},
    ][-100:]
    snapshot["last_attempt"] = result
    if result["status"] == "success":
        snapshot["spec"] = result["spec"]
        snapshot["playlist_ids"] = result["playlist_ids"]
        snapshot.pop("draft_spec", None)
        snapshot.pop("pending_question", None)
        current = set(result["playlist_ids"])
        result["changes"] = {
            "added": len(current - previous),
            "removed": len(previous - current),
            "retained": len(previous & current),
        }
    elif result["status"] in {"failed", "clarification"}:
        if result.get("spec"):
            snapshot["draft_spec"] = result["spec"]
        snapshot["pending_question"] = result["message"]
    return snapshot
