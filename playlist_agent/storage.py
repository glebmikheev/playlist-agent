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
            timestamp = data.get("updated_at", "")[:16]
            label = timestamp + " · " + spec.get("semantic_brief", "Conversation")[:55]
            choices.append((label, path.stem))
        return choices


def update_snapshot(snapshot, message, result):
    previous_ids = set(snapshot.get("playlist_ids", []))
    context = snapshot.get("context_messages", snapshot.get("messages", []))
    snapshot["messages"] = [
        *snapshot.get("messages", []),
        {"role": "user", "content": message},
        {"role": "assistant", "content": result["message"]},
    ][-100:]
    if result["status"] in {"success", "clarification", "failed"}:
        context = [
            *context,
            {"role": "user", "content": message},
            {"role": "assistant", "content": result["message"]},
        ][-12:]
    snapshot["context_messages"] = context

    usage = snapshot.setdefault(
        "usage", {"runs_used": 0, "unavailable_runs": 0, "rejected_requests": 0}
    )
    counts_toward_quota = result.get(
        "counts_toward_quota",
        result["status"] in {"success", "clarification", "failed"},
    )
    usage["runs_used"] += int(counts_toward_quota)
    usage["provider_attempts"] = usage.get("provider_attempts", 0) + result.get(
        "metrics", {}
    ).get("llm_calls", 0)
    usage["unavailable_runs"] += int(
        result.get("failure_reason") == "provider_unavailable"
    )
    usage["rejected_requests"] += int(result["status"] == "rejected")

    snapshot["last_attempt"] = result
    if result["status"] == "success":
        snapshot["spec"] = result["spec"]
        snapshot["playlist_ids"] = result["playlist_ids"]
        snapshot.pop("draft_spec", None)
        snapshot.pop("pending_question", None)
        current_ids = set(result["playlist_ids"])
        result["changes"] = {
            "added": len(current_ids - previous_ids),
            "removed": len(previous_ids - current_ids),
            "retained": len(previous_ids & current_ids),
        }
    elif result["status"] in {"failed", "clarification"}:
        if result.get("spec"):
            snapshot["draft_spec"] = result["spec"]
        snapshot["pending_question"] = result["message"]
    return snapshot
