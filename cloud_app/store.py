import hashlib
import json
import threading
import time
from datetime import datetime, timezone

from google.api_core.exceptions import Aborted
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .policy import Denied, calendar, fresh_board, prune, reserve


def pack(payload, expires, cap):
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode()) > cap:
        raise Denied("storage", status=503)
    return {
        "payload": encoded,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc),
    }


def unpack(snapshot):
    return json.loads(snapshot.to_dict()["payload"]) if snapshot.exists else None


class FirestoreStore:
    def __init__(self, config, client=None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            self.client = firestore.Client(
                project=config.project, database=config.database
            )
        self.control = self.client.collection(config.prefix + "_control").document(
            "state"
        )
        self.records = self.client.collection(config.prefix + "_records")
        self._lock = threading.Lock()

    def atomic(self, operation):
        @firestore.transactional
        def apply(transaction):
            return operation(transaction)

        # Reduce contention in one instance; Firestore still arbitrates other instances.
        with self._lock:
            try:
                return apply(self.client.transaction(max_attempts=3))
            except ValueError as error:
                if isinstance(error.__cause__, Aborted):
                    raise Denied("unavailable", status=503) from error
                raise

    def read(self, reference, transaction=None):
        return unpack(reference.get(transaction=transaction, retry=None, timeout=5))

    def board(self, transaction):
        return self.read(self.control, transaction) or fresh_board()

    def write_board(self, transaction, board):
        # The control document is never physically expired; its entries are pruned.
        transaction.set(
            self.control, pack(board, 4102444800, self.config.payload_bytes)
        )

    def run_reference(self, owner, request_id):
        key = hashlib.sha256((owner + request_id).encode()).hexdigest()
        return self.records.document("r_" + key)

    def charge_http(self, now):
        def operation(transaction):
            board = self.board(transaction)
            prune(board, now)
            _, day, _, reset, _ = calendar(now)
            bucket = board["http"].setdefault(
                day, {"used": 0, "expires": reset + 86400}
            )
            if bucket["used"] >= self.config.http_daily:
                raise Denied("http", reset)
            bucket["used"] += 1
            self.write_board(transaction, board)

        self.atomic(operation)

    def admit(
        self, owner, ip, request_id, fingerprint, session_id, now, new_session=False
    ):
        run_ref = self.run_reference(owner, request_id)

        def operation(transaction):
            board = self.board(transaction)
            previous = self.read(run_ref, transaction)
            session_ref = self.records.document("s_" + session_id)
            session = self.read(session_ref, transaction)

            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("A request ID was reused with different content.")
                return previous, False
            if session is not None and (
                session["owner"] != owner or session["expires"] <= now
            ):
                raise PermissionError("Conversation unavailable.")

            is_new = session is None
            if is_new and not new_session:
                raise PermissionError("Conversation unavailable.")

            day, provider_day = reserve(
                board, self.config, owner, ip, run_ref.id, session_id, is_new, now
            )
            expires = now + self.config.retention_days * 86400

            if session is None:
                session = {
                    "owner": owner,
                    "created": now,
                    "expires": expires,
                    "snapshot": {
                        "schema_version": 1,
                        "session_id": session_id,
                        "messages": [],
                        "playlist_ids": [],
                    },
                }
                transaction.set(
                    session_ref, pack(session, expires, self.config.payload_bytes)
                )

            run = {
                "owner": owner,
                "ip": ip,
                "fingerprint": fingerprint,
                "session_id": session_id,
                "created": now,
                "expires": expires,
                "day": day,
                "provider_day": provider_day,
                "attempts": 0,
                "status": "pending",
                "reservation": self.config.run_reservation,
            }
            transaction.set(run_ref, pack(run, expires, self.config.payload_bytes))
            self.write_board(transaction, board)
            return run, True

        return self.atomic(operation)

    def load_session(self, owner, session_id, now):
        session = self.read(self.records.document("s_" + session_id))
        if not session or session["owner"] != owner or session["expires"] <= now:
            raise PermissionError("Conversation unavailable.")
        return session

    def list_sessions(self, owner, now):
        board = self.read(self.control) or fresh_board()
        guest = board["guests"].get(owner)
        if not guest or guest["expires"] <= now:
            return []

        result = []
        for session_id in guest["sessions"]:
            try:
                session = self.load_session(owner, session_id, now)
            except PermissionError:
                continue
            snapshot = session["snapshot"]
            spec = snapshot.get("spec") or snapshot.get("draft_spec") or {}
            result.append(
                {
                    "id": session_id,
                    "label": spec.get("semantic_brief", "New conversation")[:100],
                    "expires": session["expires"],
                }
            )
        return result

    def attempt(self, owner, request_id, now):
        run_ref = self.run_reference(owner, request_id)

        def operation(transaction):
            board = self.board(transaction)
            run = self.read(run_ref, transaction)
            if (
                not run
                or run["status"] != "pending"
                or now > run["created"] + self.config.model.timeout_s
            ):
                raise Denied("busy")

            prune(board, now)
            _, provider_day, _, reset, _ = calendar(now)
            if provider_day != run["provider_day"]:
                # Do not move a reserved run into a different provider-day budget.
                raise Denied("provider", reset)

            bucket = board["providers"][provider_day]
            if (
                run["attempts"] >= 9
                or 2 * bucket["attempts"] + 2 > self.config.provider_rpd
            ):
                raise Denied("provider", reset)
            if (
                len(board["minute"]) * 2 + 2 > self.config.provider_rpm
                or sum(event[1] for event in board["minute"])
                + self.config.attempt_tokens
                > self.config.provider_tpm
            ):
                next_at = board["minute"][0][0] + 60 if board["minute"] else now + 60
                raise Denied("provider", next_at)

            board["minute"].append([now, self.config.attempt_tokens])
            bucket["attempts"] += 1
            run["attempts"] += 1
            transaction.set(
                run_ref, pack(run, run["expires"], self.config.payload_bytes)
            )
            self.write_board(transaction, board)

        self.atomic(operation)

    def finish(self, owner, request_id, result, snapshot, now):
        run_ref = self.run_reference(owner, request_id)

        def operation(transaction):
            board = self.board(transaction)
            run = self.read(run_ref, transaction)
            session_ref = self.records.document("s_" + run["session_id"])
            session = self.read(session_ref, transaction)

            if run["status"] != "pending":
                return
            if run["owner"] != owner or session["owner"] != owner:
                raise PermissionError("Conversation unavailable.")

            calls = result.get("metrics", {}).get("calls", [])
            known = sum(
                call.get("total_tokens", self.config.attempt_tokens) for call in calls
            )
            unknown = max(0, run["attempts"] - len(calls)) * self.config.attempt_tokens
            charged = min(run["reservation"], known + unknown)
            release = run["reservation"] - charged
            board["days"][run["day"]]["tokens"] -= release
            board["providers"][run["provider_day"]]["tokens"] -= release

            guest = board["guests"].get(owner)
            if not result.get("counts_toward_quota", False) and guest:
                guest["used"] = max(0, guest["used"] - 1)
            if not run["attempts"]:
                board["ips"][run["ip"]] = [
                    event
                    for event in board["ips"].get(run["ip"], [])
                    if event[0] != run_ref.id
                ]

            if board.get("active_run") == run_ref.id:
                board["active_until"] = 0

            run["status"] = "done"
            run["charged_tokens"] = charged
            run["outcome"] = result["status"]
            session["snapshot"] = snapshot
            transaction.set(
                session_ref,
                pack(session, session["expires"], self.config.payload_bytes),
            )
            transaction.set(
                run_ref, pack(run, run["expires"], self.config.payload_bytes)
            )
            self.write_board(transaction, board)

        self.atomic(operation)

    def cleanup(self, now):
        lease = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()

        def claim(transaction):
            board = self.board(transaction)
            prune(board, now)
            if board["next_cleanup"] > now or board.get("cleanup_until", 0) > now:
                return 0
            _, day, _, reset, _ = calendar(now)
            usage = board["maintenance"].setdefault(
                day, {"reads": 0, "deletes": 0, "expires": reset + 86400}
            )
            count = min(
                200,
                (1200 - usage["reads"] - 8) // 4,
                1200 - usage["deletes"],
            )
            if count <= 0:
                return 0
            usage["reads"] += 4 * count + 8
            usage["deletes"] += count
            board["cleanup_until"] = now + 120
            board["cleanup_lease"] = lease
            self.write_board(transaction, board)
            return count

        count = self.atomic(claim)
        if not count:
            return

        query = (
            self.records.where(
                filter=FieldFilter(
                    "expires_at", "<=", datetime.fromtimestamp(now, timezone.utc)
                )
            )
            .order_by("expires_at")
            .limit(count)
        )
        documents = list(query.stream(retry=None, timeout=5))

        def remove(transaction):
            board = self.board(transaction)
            if board.get("cleanup_lease") != lease:
                return
            current = [
                (doc.reference, self.read(doc.reference, transaction))
                for doc in documents
            ]
            removed = 0
            for reference, record in current:
                if record and record["expires"] <= now:
                    transaction.delete(reference)
                    removed += 1
            board["records"] = max(0, board["records"] - removed)
            board["next_cleanup"] = now + 86400 if len(documents) < count else now
            board["cleanup_until"] = 0
            self.write_board(transaction, board)

        self.atomic(remove)
