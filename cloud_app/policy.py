from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
PACIFIC = ZoneInfo("America/Los_Angeles")
WINDOWS = ((7, 10, 4), (10, 13, 3), (13, 16, 4), (16, 19, 3), (19, 22, 4), (22, 24, 2))
OWNER_RUN_HEADROOM = 4
EXPLANATION = "This is a free, publicly hosted demo. Usage limits help keep it free and give other visitors a chance to try it too."


class Denied(Exception):
    def __init__(self, reason, next_at=None, status=429):
        self.reason = reason
        self.next_at = next_at
        self.status = status

        reasons = {
            "guest": "You have reached your guest demo limit.",
            "ip": "This network has reached its eight-hour demo limit. People sharing Wi-Fi or a VPN share this allowance.",
            "day": "Today's demo allowance has been used up, likely by earlier visitors.",
            "capacity": "The service has reached its total daily run limit.",
            "window": "This time window's demo allowance has been used up.",
            "night": "Live generation is paused overnight to conserve the demo allowance.",
            "tokens": "The demo's AI usage budget has been reached.",
            "provider": "The AI provider's request or token allowance has been reached.",
            "busy": "The demo is busy right now. Please try again shortly.",
            "storage": "New requests are temporarily paused while the demo's storage is maintained.",
            "http": "This demo has reached its request allowance.",
            "unavailable": "We cannot verify the demo allowance right now. Your request was not sent to the AI model.",
        }
        message = reasons[reason] + " " + EXPLANATION
        if next_at is not None:
            retry_at = datetime.fromtimestamp(next_at, PARIS).strftime(
                "%Y-%m-%d %H:%M %Z"
            )
            message += " You can try again after " + retry_at + "."
        super().__init__(message)


def calendar(now):
    paris = datetime.fromtimestamp(now, PARIS)
    pacific = datetime.fromtimestamp(now, PACIFIC)
    tomorrow = (paris + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    provider_reset = (pacific + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window = next((item for item in WINDOWS if item[0] <= paris.hour < item[1]), None)
    return (
        paris,
        pacific.date().isoformat(),
        tomorrow.timestamp(),
        provider_reset.timestamp(),
        window,
    )


def next_window(now):
    local = datetime.fromtimestamp(now, PARIS)
    for hour, _, _ in WINDOWS:
        candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate.timestamp() > now:
            return candidate.timestamp()
    return (
        (local + timedelta(days=1))
        .replace(hour=7, minute=0, second=0, microsecond=0)
        .timestamp()
    )


def fresh_board():
    return {
        "days": {},
        "providers": {},
        "guests": {},
        "ips": {},
        "minute": [],
        "http": {},
        "maintenance": {},
        "records": 0,
        "next_cleanup": 0,
        "active_until": 0,
    }


def prune(board, now):
    for field in ("days", "providers", "http", "maintenance"):
        board[field] = {
            key: value for key, value in board[field].items() if value["expires"] > now
        }
    board["guests"] = {
        key: value for key, value in board["guests"].items() if value["expires"] > now
    }
    board["ips"] = {
        key: [event for event in events if event[1] > now - 8 * 3600]
        for key, events in board["ips"].items()
    }
    board["ips"] = {key: events for key, events in board["ips"].items() if events}
    board["minute"] = [event for event in board["minute"] if event[0] > now - 60]


def reserve(
    board, config, owner, ip, run_id, session_id, is_new, now, *, is_owner=False
):
    prune(board, now)
    local, provider_day, day_reset, provider_reset, window = calendar(now)

    if window is None and not is_owner:
        raise Denied("night", next_window(now))
    if board["active_until"] > now:
        raise Denied("busy", board["active_until"])

    day = board["days"].setdefault(
        local.date().isoformat(),
        {"runs": 0, "tokens": 0, "windows": {}, "expires": day_reset + 2 * 86400},
    )
    owner_runs = day.get("owner_runs", 0)
    if day["runs"] + owner_runs >= config.daily_runs + OWNER_RUN_HEADROOM:
        raise Denied("capacity", day_reset)

    events = []
    guest = None
    if not is_owner:
        if day["runs"] >= config.daily_runs:
            raise Denied(
                "day",
                datetime.fromtimestamp(day_reset, PARIS).replace(hour=7).timestamp(),
            )
        if day["windows"].get(str(window[0]), 0) >= window[2]:
            raise Denied("window", next_window(now))

        guest = board["guests"].get(
            owner,
            {"used": 0, "sessions": [], "expires": now + config.retention_days * 86400},
        )
        if guest["used"] >= config.guest_runs or (
            is_new and len(guest["sessions"]) >= 3 * config.guest_runs
        ):
            raise Denied("guest", guest["expires"])

        events = board["ips"].get(ip, [])
        if len(events) >= 3 * config.guest_runs:
            raise Denied("ip", events[0][1] + 8 * 3600)

    provider = board["providers"].setdefault(
        provider_day,
        {"attempts": 0, "tokens": 0, "expires": provider_reset + 2 * 86400},
    )
    if (
        max(day["tokens"], provider["tokens"]) + config.run_reservation
        > config.daily_tokens
    ):
        raise Denied("tokens", max(day_reset, provider_reset))
    if (
        board["records"] + 1 + int(is_new)
    ) * 96 * 1024 + 1024**2 > config.storage_bytes:
        raise Denied("storage", status=503)

    if is_owner:
        day["owner_runs"] = owner_runs + 1
    else:
        guest["used"] += 1
        if is_new:
            guest["sessions"].append(session_id)
        board["guests"][owner] = guest
        board["ips"][ip] = [*events, [run_id, now]]
        day["runs"] += 1
        window_key = str(window[0])
        day["windows"][window_key] = day["windows"].get(window_key, 0) + 1

    day["tokens"] += config.run_reservation
    provider["tokens"] += config.run_reservation
    board["records"] += 1 + int(is_new)
    board["active_until"] = now + config.model.timeout_s + 30
    board["active_run"] = run_id
    return local.date().isoformat(), provider_day
