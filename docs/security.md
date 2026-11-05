# Request handling and security

The local app uses English UI text and English model explanations. Artist and
track names retain their catalogue spelling. The pinned Gradio frontend's locale
is set to English at page load; other locale controls are hidden.

## SQL boundaries

Catalogue values, reference text, IDs, filters and limits use SQLite parameters.
Dynamic table/field names come from application constants. Audio-feature column
names are checked against `FEATURES` at the SQL boundary, in addition to the
Pydantic literal type. The catalogue connection is read-only. The model supplies
typed preferences and cannot submit SQL.

Audit inserts also bind every value. Private regression checks cover malicious
genre/artist/reference/ID strings, an injected column name and an injected audit
classification. These checks establish the tested boundaries, not a claim that
the entire application is vulnerability-free.

## Content decisions

The interpretation response must include a Gemini moderation decision, category
and optional short English reason. This shares the first existing model call.
A blocked request ends before retrieval/ranking. Missing or invalid decisions
fail closed. Native provider safety blocks retain available categories/ratings.
A provider block while ranking catalogue metadata is treated as a generation
failure, rather than an accusation that the user's input was inappropriate.

Music titles, explicit-track preferences, dark moods and legitimate educational
context are not automatically harmful. The classifier still depends on the
model and can make mistakes; SQL restrictions and resource limits are enforced
independently of its decision.

Rejected messages remain visible in their conversation but are excluded from
the later model context. Error turns also stay out of that context; their input
remains in the textbox for an explicit retry. The fixed English rejection notice identifies the
category and says that the incident/IP were recorded only after a successful
audit write. If storage fails, the request still stays rejected and the notice
does not claim that logging succeeded.

## Private audit data

`data/security/audit.sqlite` holds event ID, UTC timestamp, model classification,
reason, submitted text, connection IP and available HTTP method, path, protocol,
headers, query parameters and body. A small body's SHA256 and size are recorded.
When the original body is unavailable, the record explicitly labels the handler
payload as a reconstruction.

Authorization/cookie headers, credential fields and common API-key token formats
are redacted. Bodies over 16 KiB use the bounded handler payload instead; nested
metadata is bounded and large events are truncated at the metadata level.
The database retains the latest 1,000 events. This is a sanitized application
audit, not an unlimited packet capture. It stays outside Git and the image.

The local server does not trust forwarded-IP headers by default. The recorded
address is the ASGI connection address, which can be a proxy/NAT address. A public
deployment must deliberately configure trusted proxies before treating forwarded
addresses as visitor IPs.

Gradio file access blocks the audit/session folders, secrets, Git metadata and
private development directories. Exports contain playlist specifications and
track metadata, not audit records. The local Docker command mounts a separate
audit volume. Public GCP hosting needs durable private audit storage and its own
admission controls before exposure.

References:

- [Gemini safety settings and categories](https://aistudio.google.com/docs/safety-settings)
- [Gradio request metadata](https://gradio.app/docs/gradio/request)
