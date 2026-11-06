import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playlist_agent.settings import Settings


@dataclass(frozen=True)
class CloudSettings:
    project: str
    origin: str
    cookie_secret: str = field(repr=False)
    model: Settings
    provider_rpm: int
    provider_tpm: int
    provider_rpd: int
    database: str = "(default)"
    prefix: str = "playlist_demo"
    guest_runs: int = 2
    daily_runs: int = 20
    daily_tokens: int = 1_200_000
    http_daily: int = 400
    trusted_xff_hops: int = 0
    secure_cookie: bool = True
    retention_days: int = 7
    storage_bytes: int = 128 * 1024**2
    payload_bytes: int = 64 * 1024
    owner_access_key: str = field(default="", repr=False)
    allowed_origins: tuple[str, ...] = field(init=False)

    def __post_init__(self):
        origins = tuple(origin.strip() for origin in self.origin.split(","))
        for origin in origins:
            url = urlsplit(origin)
            is_bare_origin = origin == f"{url.scheme}://{url.netloc}"
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or not is_bare_origin
                or url.username is not None
                or "*" in url.netloc
            ):
                raise ValueError(
                    "PUBLIC_ORIGIN must contain bare origins without trailing slashes."
                )
            if self.secure_cookie and url.scheme != "https":
                raise ValueError("Production cookies require HTTPS origins.")
            if not self.secure_cookie and (
                url.hostname not in {"127.0.0.1", "localhost", "testserver"}
                or os.getenv("K_SERVICE")
            ):
                raise ValueError(
                    "Insecure cookies are restricted to local development."
                )
        object.__setattr__(self, "allowed_origins", origins)

        if len(self.cookie_secret) < 32 or not self.model.api_key:
            raise ValueError(
                "A server cookie secret (32+ characters) and Gemini API key are required."
            )
        if self.owner_access_key and (
            not 32 <= len(self.owner_access_key) <= 256
            or self.owner_access_key in {self.cookie_secret, self.model.api_key}
        ):
            raise ValueError(
                "OWNER_ACCESS_KEY must be a separate key of 32–256 characters."
            )
        if (
            not 1 <= self.daily_runs <= 20
            or not 1 <= self.guest_runs <= 3
            or not 1 <= self.retention_days <= 7
        ):
            raise ValueError(
                "Demo limits may not exceed the reviewed retention/admission envelope."
            )
        if not 1 <= self.http_daily <= 400 or not 0 <= self.trusted_xff_hops <= 5:
            raise ValueError("Invalid HTTP or trusted proxy limit.")
        if os.getenv("K_SERVICE") and self.trusted_xff_hops == 0:
            raise ValueError(
                "Verify and configure the trusted proxy chain before publishing on Cloud Run."
            )
        if os.getenv("K_SERVICE") and os.getenv("FIRESTORE_EMULATOR_HOST"):
            raise ValueError("A Firestore emulator is not allowed on Cloud Run.")
        if (
            not 0 < self.storage_bytes <= 128 * 1024**2
            or not 1024 <= self.payload_bytes <= 64 * 1024
        ):
            raise ValueError("Invalid storage envelope.")
        if (
            self.provider_rpm < 2
            or self.provider_tpm < self.attempt_tokens
            or self.provider_rpd < 1
            or self.daily_tokens < self.run_reservation
        ):
            raise ValueError(
                "Provider quotas must be positive and the daily token budget must cover a run reservation."
            )
        if (
            not 0 < self.model.timeout_s <= 180
            or not 0 < self.model.max_input_tokens <= 24000
            or not 0 < self.model.max_output_tokens <= 4096
        ):
            raise ValueError("Model settings exceed the reviewed demo limits.")

    @property
    def attempt_tokens(self):
        return self.model.max_input_tokens + self.model.max_output_tokens

    @property
    def run_reservation(self):
        return 9 * self.attempt_tokens

    @classmethod
    def load(cls):
        # Cloud credentials come only from the server environment, never a local .env.
        model = Settings(
            api_key=os.environ["GEMINI_API_KEY"],
            model=os.environ["GEMINI_MODEL"],
            thinking_level=os.getenv("GEMINI_THINKING_LEVEL", "low"),
            thinking_budget=int(os.getenv("GEMINI_THINKING_BUDGET", "1024")),
            max_input_tokens=int(os.getenv("GEMINI_MAX_INPUT_TOKENS", "24000")),
            max_output_tokens=int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096")),
            timeout_s=int(os.getenv("AGENT_TIMEOUT_SECONDS", "180")),
        )
        return cls(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            origin=os.environ["PUBLIC_ORIGIN"],
            cookie_secret=os.environ["COOKIE_SECRET"],
            model=model,
            provider_rpm=int(os.environ["PROVIDER_RPM"]),
            provider_tpm=int(os.environ["PROVIDER_TPM"]),
            provider_rpd=int(os.environ["PROVIDER_RPD"]),
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
            trusted_xff_hops=int(os.getenv("TRUSTED_XFF_HOPS", "0")),
            secure_cookie=os.getenv("LOCAL_HTTP", "0") != "1",
            owner_access_key=os.getenv("OWNER_ACCESS_KEY", ""),
        )
