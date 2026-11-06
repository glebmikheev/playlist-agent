# GCP version: manual Console setup

The cloud application is implemented in `cloud_app/`. No cloud project, database,
service account, secret, registry, Cloud Run service or log bucket is created by
the application. Configure those yourself in the web consoles. There are no
deployment/provisioning scripts. The local Gradio entry point remains `app.py`.

The cloud version uses a small English HTTP interface rather than exposing the
local Gradio endpoints. It shares the catalogue, Gemini adapter, playlist graph,
validation and presentation functions. Its cookie ownership, Firestore admission,
structured logging, downloads and dependencies are separate from the local app.

## Build the image locally

From the repository root:

```powershell
docker build -f Dockerfile.cloud -t playlist-agent:cloud-local .
```

Upload the image to your chosen registry separately. The cloud image contains
the prepared read-only SQLite catalogue and runtime code. It contains no `.env`,
service-account key, CSV source, notebook, report or developer test. It runs as
UID/GID 10001, binds `0.0.0.0:$PORT`, and writes neither conversations nor exports
to local files. Dependency versions are pinned in `requirements-cloud.lock`.

## Firestore

Use an eligible Firestore **Standard / Native mode** database in your project.
The default database ID is `(default)`. Choose a location deliberately; it cannot
be treated as an application runtime setting. Prefer a location near Cloud Run.
The free quota is shared with other workloads in the same eligible database.

The application creates its own documents on first use, not the database itself:

| Collection | Content |
| --- | --- |
| `playlist_demo_control` | A single transactionally updated `state` document: budgets, compact guest/IP histories, storage count and cleanup lease |
| `playlist_demo_records` | Conversation snapshots (`s_...`) and owner-bound idempotent run records (`r_...`) |

Each document has a bounded JSON-string `payload` and `expires_at` timestamp.
Indexes are needed only on `playlist_demo_records.expires_at` for cleanup; owner
listing comes from that guest's bounded conversation IDs in the control record.
The index exemptions in `cloud_app/firestore.indexes.json` describe fields to
disable in the Console. Do not disable the records' expiration index.

Do **not** enable managed TTL, PITR or backups for the free demonstration. Cleanup
uses ordinary deletes, with its own operation budget. If Firebase client SDK
access is enabled, use the deny-all rules supplied in `cloud_app/firestore.rules`.
Browser clients never access Firestore directly. The server SDK authenticates
through IAM and enforces owner access in application code.

## Service identity and secrets

Use a dedicated Cloud Run service account. Grant Firestore data access
(`roles/datastore.user`) in the intended project. Grant Secret Manager Secret
Accessor only for the two secrets it must receive:

- `GEMINI_API_KEY`: a key for the selected Gemini Developer API project/model.
- `COOKIE_SECRET`: at least 32 cryptographically random characters, generated
  outside the browser application. Keep it stable across revisions.

Select secret versions explicitly in Cloud Run's environment/secrets settings.
Do not use a service-account JSON key file, grant visitors IAM access, or place
these values in the image. Changing `COOKIE_SECRET` invalidates all existing guest
cookies and changes IP hashes; existing records remain protected and expire.

Keep the Gemini project on its intended Free tier, with no paid fallback. The
application cannot verify or change an AI Studio project's billing tier. Confirm
the actual model availability and quotas in AI Studio before entering the values
below. Invalid zero placeholders deliberately prevent startup.

## Cloud Run fields

Select your uploaded image and the dedicated service identity. Suggested initial
settings, to verify against your project and measurements:

| Console field | Value |
| --- | --- |
| Container port | 8080 |
| Billing | Request-based |
| Service minimum / maximum instances | 0 / 1 |
| CPU / memory | 1 vCPU / 1 GiB initially |
| Maximum concurrent requests | 8; the application admits only one model run at a time across instances |
| Request timeout | 240 seconds; the agent deadline is at most 180 seconds |
| Startup probe | HTTP GET `/health` |
| Public access | Only after origin, cookies, IAM and quota behavior have been checked |

`/health` needs neither cookies nor Firestore, so quota exhaustion cannot cause
a liveness/restart loop. Do not configure periodic external warm-up requests.
The interface uses ordinary finite fetch requests, without a WebSocket, event
stream or polling loop that holds the container active after completion.

## Environment variables

The complete example is `.env.cloud.example`; it contains no usable secrets.
Set the following in the service's environment/secrets section:

| Variable | Setting |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Your Firestore project ID |
| `FIRESTORE_DATABASE` | `(default)`, unless you deliberately selected another database |
| `PUBLIC_ORIGIN` | Exact HTTPS origins used by visitors, separated by commas, without trailing slashes, paths or queries |
| `GEMINI_MODEL` | Explicit model ID tested for your Gemini key |
| `GEMINI_API_KEY`, `COOKIE_SECRET` | Secret references, not frontend values |
| `OWNER_ACCESS_KEY` | Optional reference to a separate randomly generated secret |
| `PROVIDER_RPM`, `PROVIDER_TPM`, `PROVIDER_RPD` | Your actual free project/model allowances; never guessed from another model |
| `TRUSTED_XFF_HOPS` | Verified client-IP position from the right of `X-Forwarded-For`; see below |
| `GEMINI_MAX_INPUT_TOKENS` | 24000 by default; may be lowered |
| `GEMINI_MAX_OUTPUT_TOKENS` | 4096 by default; may be lowered |
| `AGENT_TIMEOUT_SECONDS` | 180 by default; may be lowered |
| `GEMINI_THINKING_LEVEL` | `low` by default |
| `LOCAL_HTTP` | Leave absent or 0 on Cloud Run; 1 is refused there |

The cookie is `__Host-playlist_guest`, with Secure, HttpOnly, SameSite=Lax,
Path=/ and no Domain. Each hostname has its own guest cookie. POSTs require an
Origin from the configured list and a custom same-origin request header.
Avoid a cross-origin iframe for this demo. If you do
not know the service URL until a revision exists, keep public access disabled,
then copy the URL from the Console and update `PUBLIC_ORIGIN` before testing.
There is no fallback that trusts an arbitrary Host/Origin header.

### Owner access

Generate a separate key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Save it in Secret Manager and grant the Cloud Run service account
`roles/secretmanager.secretAccessor` on that secret. Reference a specific version
as `OWNER_ACCESS_KEY`. After deploying, open the demo with `/#owner` and enter the
key once. The server issues a signed HttpOnly cookie valid for eight hours on
that hostname. Rotating the key invalidates existing owner cookies after deploying
the new version. The existing guest cookie still identifies the conversations
in that browser.

Owner runs skip the public schedule, time windows, daily allowance, and guest
and network generation quotas. The service allows up to four more total runs
per Paris day when owner access is used, with a maximum of 24 across all users
at the default configuration. These runs still use the shared token, provider,
HTTP, and storage budgets and the single-run lease.

### Trusted client IP: verify the actual ingress path

Uvicorn's automatic proxy-header trust is disabled. The app reads the direct peer
when `TRUSTED_XFF_HOPS=0` (local development). On Cloud Run a positive, explicitly
configured value is required. The code selects that address from the **right**
of `X-Forwarded-For`; values farther left are never trusted automatically.

Do not guess the hop count or trust the first caller-supplied address. Verify
what the Google-managed ingress appends in your chosen topology. With a load
balancer, restrict ingress/default URL access so a shorter direct route cannot
bypass the expected chain. All allowed entry paths must have the verified
suffix. Test that adding fake addresses on the left does not change the IP
allowance. Missing/malformed addresses are rejected; they never become a new
anonymous quota identity. IPv4-mapped IPv6 is normalized, and native IPv6 uses a
/64 network for the anti-abuse budget. IP is never an authorization identity.

## Implemented admission behavior

- At most 20 newly admitted guest runs per Paris calendar day, distributed 4/3/4/3/4/2
  over the agreed 07–10/10–13/13–16/16–19/19–22/22–00 windows. Closed 00–07.
- Two charged runs per guest cookie lifetime. At most six started model runs per
  IP/network in a rolling eight-hour window, across different guest cookies.
- Global model-run lease, maximum three logical operations, at most nine provider
  attempts under the shared deadline. A lost/duplicated HTTP request reuses the
  same request ID; an interrupted or uncertain run is never replayed automatically.
- Before admission reserve nine times the configured input+output token ceiling
  against both Paris-day and provider-day budgets (1,200,000 each by default).
  At the defaults that is 252,864 tokens. Known usage releases unused reservation;
  missing usage remains conservatively charged. The daily run counts include admitted
  failures to bound records and resource use; it is not refunded with the guest.
- Before **each** provider attempt, check durable provider-minute/day limits.
  RPM/RPD conservatively count token-count and generation as two requests.
  TPM reserves the configured full attempt token ceiling. These choices may
  admit fewer runs than the provider would allow; they do not enlarge its quota.
  A short internal minute gate may wait once within the same run deadline before
  authorizing the next call. This makes low-RPM projects usable without retrying
  a provider 429 or creating an unlimited polling loop.
- Errors/rejections return the guest allowance. IP usage is retained once work
  started; known consumed provider resources are never refunded. Unknown/crashed
  outcomes keep their reservations. There is no automatic 429 or paid fallback.
- At most 400 authenticated backend HTTP actions per Pacific quota day, including
  restores/exports/denials. Static pages do not create records. In-process global
  and IP throttles reject bursts before Firestore. An exhausted HTTP budget is
  cached locally until reset; across restarts it is rechecked durably.

Global ceilings and record caps deliberately remain in `CloudSettings` rather
than accepting arbitrary browser values. Configure smaller model ceilings if
needed to fit the actual free provider allowance; values above the reviewed
ceilings fail startup. Tiny allowances can make a multi-call run impossible.

## Retention, storage and logging

Records expire seven days after creation, independent of subsequent edits.
Expired conversations are inaccessible immediately. A request-triggered cleanup
lease checks for expired records without waking an idle service. It deletes
small pages, reserving a conservative read/retry allowance and at most 1200
reads/deletes per Pacific day. A backlog may take multiple requests/days to drain.

Conversations keep bounded recent context and accepted playlist IDs. Each payload
is capped at 64 KiB, each data record reserves a 96 KiB envelope including metadata
and indexes, and total accounted record storage stays below a 128 MiB ceiling.
The fixed control record is also capped. Larger data or unavailable storage
declines work rather than silently using memory/local files. The implementation
creates at most two new records per new-conversation admission, within the more
conservative six-record planning envelope in `cloud-demo.md`.

Moderation events are redacted structured JSON on stdout, collected by Cloud Run
into Cloud Logging. Cookies, authorization, keys and recognizable credential
values are redacted; oversized header/query metadata is omitted. The event has
a random event ID, category, reason, bounded request body, method/path and trusted
connection IP. Do not copy those events into Firestore. Verify collection in Logs
Explorer and configure log retention/exclusions in the web console. Avoid logging
request bodies/secrets elsewhere; exclude noisy health/static request logs if useful.

These controls bound model admissions and retained application data. They do not
guarantee a zero total bill under arbitrary public traffic: platform request logs,
restarts, rejected traffic, image storage/builds, secrets and other project usage
have separate costs/allowances. Configure billing alerts/spend controls separately
and review enforcement limitations. The application does not change billing.

## Checks before enabling public access

1. `/health` returns 200; the English interface loads and the cookie has the
   production attributes. No credentials or local files are downloadable.
2. A real small request succeeds. Reloading and exporting do not call Gemini.
3. Another browser cannot list, open, change or export the first browser's dialog.
4. Duplicate request IDs do not generate a second model run. Deleting cookies
   does not reset the IP or global budget. Concurrent requests cannot overspend.
5. Verify quota messages/timestamps, a cold start, expired conversations and audit
   collection. Never lower a quota by clearing Firestore while runs are active.

Local verification uses the Firestore emulator with a `demo-` project and mocked
Gemini. No cloud deployment or real IAM/secrets/log-sink verification has been
performed by this implementation. The emulator tests are private under ignored
`tests/`; the Docker image contains no such fixtures or model bypass.

Verification for this iteration: all 45 existing regression checks and 19 cloud
checks passed. The cloud checks also passed inside the final Linux image against
the local emulator. They cover concurrent admission/replay, cookie forgery and
expiry, cross-guest reads/exports, IP history, conservative refunds, storage
failure before provider access, cleanup and HTTP-budget behavior. Ruff, Python
compilation and JavaScript syntax checks passed. The browser view was inspected;
container HTTP probes returned 200 for the UI/health and 404 for private paths.

References: [Firestore transactions](https://firebase.google.com/docs/firestore/manage-data/transactions),
[server IAM](https://docs.cloud.google.com/firestore/native/docs/security/iam),
[free quota and TTL](https://firebase.google.com/docs/firestore/quotas#free-quota),
[proxy header behavior](https://docs.cloud.google.com/functions/docs/reference/headers),
[OWASP session cookies](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
