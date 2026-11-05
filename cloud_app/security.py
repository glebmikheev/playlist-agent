import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import uuid
from collections import OrderedDict, deque

from playlist_agent.security import redact

from .policy import Denied


def cookie_name(config):
    if config.secure_cookie:
        return "__Host-playlist_guest"
    return "playlist_guest_local"


def new_cookie(config, now):
    body = secrets.token_urlsafe(32) + "." + str(int(now))
    signature = hmac.new(
        config.cookie_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return body + "." + signature


def cookie_owner(config, token, now):
    if not token or not re.fullmatch(r"[\w-]{43}\.\d{10}\.[a-f0-9]{64}", token):
        return None

    body, signature = token.rsplit(".", 1)
    expected = hmac.new(
        config.cookie_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    issued = int(body.rsplit(".", 1)[1])
    if (
        not hmac.compare_digest(signature, expected)
        or not 0 <= now - issued < config.retention_days * 86400
    ):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request, config):
    if config.trusted_xff_hops:
        values = request.headers.get("x-forwarded-for", "").split(",")
        if len(values) < config.trusted_xff_hops:
            raise ValueError("Client address unavailable.")
        value = values[-config.trusted_xff_hops].strip()
    else:
        value = request.client.host if request.client else ""

    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.compressed


def ip_key(config, address):
    ip = ipaddress.ip_address(address)
    # Rotating IPv6 interface addresses should not create unlimited allowances.
    if ip.version == 6:
        value = str(ipaddress.ip_network(f"{ip}/64", strict=False))
    else:
        value = str(ip)
    return hmac.new(
        config.cookie_secret.encode(), ("ip:" + value).encode(), hashlib.sha256
    ).hexdigest()


class TrafficGuard:
    def __init__(self):
        self.global_events = deque()
        self.clients = OrderedDict()
        self.closed_until = 0

    def check(self, key, now, check_closed=True):
        if check_closed and self.closed_until > now:
            raise Denied("http", self.closed_until)

        while self.global_events and self.global_events[0] <= now - 60:
            self.global_events.popleft()
        events = self.clients.pop(key, deque())
        while events and events[0] <= now - 60:
            events.popleft()

        if len(self.global_events) >= 120 or len(events) >= 30:
            self.clients[key] = events
            raise Denied("http", now + 60)

        events.append(now)
        self.global_events.append(now)
        self.clients[key] = events
        while len(self.clients) > 1024:
            self.clients.popitem(last=False)


def audit(request, body, moderation, address, model):
    event_id = str(uuid.uuid4())
    event = {
        "severity": "WARNING",
        "message": "Playlist request rejected",
        "event_id": event_id,
        "category": moderation["category"],
        "moderation": moderation,
        "model": model,
        "httpRequest": {
            "requestMethod": request.method,
            "requestUrl": request.url.path,
            "remoteIp": address,
            "userAgent": request.headers.get("user-agent", "")[:256],
        },
        "request": {
            "headers": dict(request.headers),
            "query": dict(request.query_params),
            "body": body,
            "http_version": request.scope.get("http_version"),
        },
    }
    # Cloud Run routes flushed structured stdout to Cloud Logging; no GCP SDK needed.
    event = redact(event)
    if len(json.dumps(event, ensure_ascii=False).encode()) > 16384:
        event["request"]["headers"] = {"omitted_for_size": True}
        event["request"]["query"] = {"omitted_for_size": True}
    print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)
    return event_id
