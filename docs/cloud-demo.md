# Public demo deployment

Design notes checked on 4 November 2026. Infrastructure has not been deployed.
The demo uses Gemini Developer API and a Cloud Run service. Actual model access
and RPM/TPM/RPD limits must be checked in the selected AI Studio project.

The independent implementation now lives in `cloud_app/`. See
[manual Console setup](gcp-console.md) for actual defaults, record layout,
verification and differences from the conservative planning estimates below.
No GCP infrastructure is created by the application.

## Implementation order and application boundary

First build and evaluate the local Gemini application. Record latency, tokens,
valid structured responses, constraint results and human-reviewed semantic fit.
Include multi-phase generation, edits and 50/80-track cases. Local benchmark
calls also consume the provider's free quota; use bounded batches.

Only then choose cloud limits and build the GCP application. Share the catalogue,
request contract, Gemini adapter and playlist workflow through a common package.
Keep the local and cloud entry points separate. Cloud admission, durable state,
credentials and deployment live in the cloud application; importing/running the
local app must not require GCP infrastructure or cloud SDK dependencies.

## Idle behavior and runtime

Use request-based billing, service minimum instances 0 and service maximum 1.
Cloud Run can scale to zero when idle and starts an instance on a later request.
With request-based billing and minimum 0, idle instances are not billed. The first
request after shutdown incurs a cold start; it should be measured with the real
application rather than assigned a guessed duration.

Start with 1 vCPU and 1 GiB RAM as a measurement baseline. Compare a 0.5 vCPU
configuration if local selection and plotting are cheap enough. Fractional CPU
requires first-generation execution, request-based billing and concurrency 1;
0.5 vCPU supports at most 1 GiB. It is an option to benchmark, not the default
deployment decision.

Build the catalogue once. Its current SQLite file occupies 46,624,768 bytes
(about 44.5 MiB). The agreed arrangement is to include this read-only file in
the Docker image. `Dockerfile` copies only the prepared database and application
runtime. There is no catalogue download at container startup. Publish a new image
to update the catalogue; measure total image size and cold-start behavior.

Cloud Storage Always Free includes 5 GB-months of Standard storage, 5,000 Class A
and 50,000 Class B operations, in us-west1, us-central1 and us-east1. European
regions are outside this allowance. The image approach removes the need for a
catalogue bucket and lets us evaluate a European Cloud Run region separately.
Container registry storage/build usage remains part of the cloud budget.

The local container is a development artifact, not the public GCP application.
The latter still needs its independent entry point and durable quota admission.
Quotas and conversations must use external durable storage; embedding the
read-only catalogue does not make writable container files persistent.

Exclude source CSVs, notebooks, reports and development/test dependencies from
the runtime image.
Viewing, plotting and exporting an existing result must not call the LLM again.

Do not add periodic warm-up requests. Defer transport choices to the cloud stage
after measuring Gradio's actual idle HTTP behavior. The local MVP does not need
custom connection-management machinery, and a completed operation leaves its
result and conversation available. Waiting for Gemini inside an active HTTP
request still contributes to the request's billable duration.

## Persistence and admission

Local JSON snapshots remain sufficient for local development. The public service
needs external durable state in Firestore: quota counters, run records
and any conversations that must survive instance replacement. The catalogue is
read-only; no quota may rely on a local file or warm-process memory.

Before calling a model, atomically reserve the run's configured worst-case token
budget and check global/session/window/provider quotas. Local settings now allow
three logical operations with two 503 retries each: at most nine generation
attempts under the same run deadline. Recalculate token/provider-request
reservations with this ceiling before public deployment. Moderation is part of
the first model operation and must also pass admission. Repeated submissions
reuse a run ID. If the outcome of a timed-out call is unknown, retain the
conservative reservation. If the quota store is unavailable, decline model work.
An unavailable run may return the user's allowance, but it must not return
already-consumed global/provider attempts or unknown token reservations. Security
audits also need durable private storage; the local audit database inside a
Cloud Run instance is insufficient.

The API key stays server-side in a Gemini project on Free tier without linked
paid billing. No paid model fallback or automatic billing upgrade is part of the
application. Anonymous session limits provide a convenience limit, not proof of
unique identity. Add an anti-bot admission check for the public endpoint.

## Agreed operating windows

Use `Europe/Paris`, including daylight-saving changes. The following is the
agreed distribution for an upper limit of 20 daily runs. Lower model quotas or
token budgets can reduce actual admissions. Future windows cannot be borrowed.

| Local interval | Run allowance |
| --- | ---: |
| 07:00-10:00 | 4 |
| 10:00-13:00 | 3 |
| 13:00-16:00 | 4 |
| 16:00-19:00 | 3 |
| 19:00-22:00 | 4 |
| 22:00-00:00 | 2 |
| 00:00-07:00 | 0 |

Track Gemini's provider-day quota separately from the Paris calendar day. Return
the next actual admission time and a clear free-demo explanation when closed or
exhausted. A prepared example remains available as a labeled example.

## Guest ownership and IP budget

Use a cryptographically random guest secret in an HttpOnly, Secure, SameSite=Lax
cookie. Derive its owner identifier server-side; never accept a client-supplied
owner ID as authorization. Conversation IDs are separate identifiers, and every
list/read/write/export checks ownership. Do not log cookie secrets. Firestore
server SDK access requires these application checks, irrespective of client
Security Rules. The catalogue remains in the image; cloud audits go to private
Cloud Logging rather than a writable audit database in the container.

In addition to a guest limit of S runs, admit at most 3*S runs per trusted client
IP in a rolling eight-hour window. Different cookies share that IP allowance.
Only trust forwarded IP information from the configured Cloud Run proxy chain.
Use a keyed hash of the normalized IP for quota keys. Started model work remains
charged to this anti-abuse counter even when an unavailable model returns the
guest allowance. Declines before model work do not consume a run. Global run,
provider-attempt and token reservations are independent of both cookies and IPs.

## Public limit messages

These are agreed cloud UI texts, not implemented admission behavior in the local
application. All quota/schedule denials include this shared explanation:

> This is a free, publicly hosted demo. Usage limits help keep it free and give
> other visitors a chance to try it too.

Prepend a specific reason, then show the next eligible date/time and timezone
when it can be calculated. Show an existing playlist/prepared example without
making a model call. Do not promise the next attempt will succeed: other budgets
or provider availability can still prevent admission.

| Reason | English text before the shared explanation |
| --- | --- |
| Guest allowance | You have reached your guest demo limit. You can try again after {time}. |
| IP allowance | This network has reached its eight-hour demo limit. People sharing Wi-Fi or a VPN share this allowance. You can try again after {time}. |
| Daily global allowance | Today's demo allowance has been used up, likely by earlier visitors. The next daily allowance starts at {time}. |
| Current Paris window | This time window's demo allowance has been used up. The next eligible window opens at {time}. |
| Token budget | The demo's AI usage budget has been reached. New requests are paused until {time}. |
| Provider quota, reset unknown | The AI provider has temporarily limited this free demo. Please try again later. |
| Per-request resource limit | This request exceeds the demo's per-request limits. Please try a shorter or simpler request. |
| Overnight closure | Live generation is paused overnight to conserve the demo allowance. It resumes at 07:00 Paris time. |
| Storage/maintenance budget | New requests are temporarily paused while the demo's storage is maintained. Please try again later. |

Infrastructure failures get their actual reason rather than being labeled quota
exhaustion. If quota storage is unavailable, say the allowance could not be
verified and that no request was sent to the AI model. Exact retry times are
omitted when unknown. Guest allowance refunds never imply that provider usage,
IP anti-abuse usage or uncertain token reservations were refunded.

## Bounded Firestore retention

This is a proposed schema/retention contract to implement and check before
deployment. Current Firestore Standard free usage is 1 GiB storage, 50,000 reads,
20,000 writes and 20,000 ordinary deletes per day for one eligible database.
Its operation quotas reset around midnight Pacific time, separately from the
Paris admission day. Managed TTL deletes have no free usage, so do not enable
Firestore TTL, PITR, backups or automatic clones for this demo.

Store only bounded conversation context, playlist IDs, specifications, usage
and idempotent run records. Hydrate track metadata from the SQLite catalogue;
do not duplicate the catalogue, exports, raw provider responses or security
audits in Firestore. Exempt conversation payloads, arrays and large maps from
indexing; keep only indexes actually required for owner queries and expiration.
The storage budget must include document names, metadata and all retained indexes.

Initial design parameters:

| Symbol | Proposed value | Meaning |
| --- | ---: | --- |
| G | 20/day | All newly admitted runs, including ones that later fail; guest refunds do not refund this creation budget |
| d | 6 | Maximum new documents attributable to one admission; repeated model attempts update the run record rather than create more documents |
| B | 96 KiB | Conservative per-document storage envelope, including indexes/metadata; payload at most 64 KiB and schema/index overhead must fit the remainder |
| R | 7 days | Absolute lifetime of conversations and ordinary run records, measured from creation; edits do not indefinitely extend it |
| K | 1 day | Maximum interval between cleanup checks while the app is serving requests |
| F | 1 MiB | Reserved envelope for a fixed, bounded set of control records |
| M | 128 MiB | Application high-water budget, leaving headroom below the free storage allowance |

For arbitrary alignment with Paris calendar days, a conservative storage bound is

```text
N <= G * d * (R + K + 1)
storage <= F + N * B
        <= 1 MiB + 20 * 6 * 9 * 96 KiB
        = 102.25 MiB < M = 128 MiB
```

Thus growth is O(G*d*B*(R+K)), rather than unbounded with page visits. More
generally choose K so F+G*d*B*(R+K+1) <= M; if that has no positive solution,
reduce retention/document size/admissions rather than borrow the free-tier
headroom. These are design bounds, not measurements of a deployed schema.
Verify d and B using the actual serializers and Firestore storage-size rules.

Cookie creation, empty conversations and denied requests must not create owner,
IP, session or run documents. Check global admission first in the transaction,
then reserve both model resources and the storage envelope before creating any
records. New control records also count against a bounded creation allowance.
Refuse new admissions if cleanup cannot keep the accounted storage below M.
Updating a conversation must enforce its size cap, not append unlimited history.

Use expires_at as an application field, not a managed TTL policy. On the first
request after a cleanup interval, acquire a distributed cleanup lease and delete
expired records in pages of at most 200. Cap maintenance at 1,200 document reads
and 1,200 deletes per Firestore quota day, accounting for queries/retries and
control writes separately. At G*d <= 120 new documents/day on a Paris day,
that covers normal churn and approximately one retained-window catch-up batch.
For a Pacific quota day allow for up to two partial Paris admission days.

Expired conversations become inaccessible immediately, even if physical cleanup
is deferred. With no requests, an idle Cloud Run service need not wake solely for
cleanup: no new admission documents accumulate. Before admitting new work after
inactivity, process the bounded backlog; if the maintenance budget is insufficient,
stay closed until it can be cleared. This bounds storage without promising a
physical deletion deadline while the service is asleep.

Never delete a still-effective quota counter, an in-flight reservation or an
idempotency record needed to reconcile uncertain usage. Set their expiration
after the latest applicable quota/hold/retry interval, retaining longer when
necessary. A cleanup failure must not reset exhausted limits.
If records must outlive R for unresolved usage, include a separate bounded
reservation for them or recompute the R-based bound. The M admission ceiling
still applies; retaining unresolved state must not create unbounded exceptions.

This bounds retained data, not arbitrary public HTTP traffic. Denied requests,
repeated restores, exports, transaction retries and console inspection can still
consume reads/operations. Those need their own throttles and operation budgets.
Cloud Logging retention, byte volume and abuse-event rates are separate limits;
Firestore cleanup does not constrain them.

## Revised limits to measure

The earlier proposals of no thinking, a 60-second run and a 1,024-token response
are not fixed requirements. Reasoning and the proposed 4,096-token generation
cap are tested locally first. Benchmark small requests and 50/80-track requests,
including edits and multi-phase constraints, before freezing cloud limits.

- Compare minimal/off reasoning with a low reasoning setting on the same cases.
  Use low reasoning where it materially improves request interpretation or ranking.
- Proposed initial application deadline: 180 seconds. Set the HTTP timeout above
  it to allow cancellation/cleanup. HTTP timeout alone does not terminate work.
- Proposed initial generation cap: up to 4,096 output tokens per call, accounting
  for reasoning within the model/API's output limit. Confirm valid complete JSON
  and record the actual usage before setting a tighter cap.
- Return compact candidate aliases/order/scores. Hydrate titles, artists and
  links from SQLite. A short indexed ranking of 80 tracks is much smaller than
  80 full track descriptions. Size the candidate pool and input allowance for
  the requested playlist length.
- Recalculate worst-case token reservations and daily token limits with these
  settings. The earlier 16,000/run and 320,000/day figures are provisional and
  must not be carried forward unchanged if they no longer cover the configuration.

At 20 runs/day, 180 seconds/run and 31 days, the agent-request component alone
would use 111,600 vCPU-seconds (31 vCPU-hours) at 1 vCPU and 111,600 GiB-seconds
at 1 GiB. This excludes starts, shutdowns, UI/denied traffic and other products.

These controls reduce consumption but do not guarantee a zero bill for an
unrestricted public Cloud Run endpoint on a paid billing account. Spend caps
are an additional safeguard, with enforcement delay and billable overages.

## References

- [Minimum instances and idle billing](https://docs.cloud.google.com/run/docs/configuring/min-instances)
- [Request-based billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings)
- [Fractional CPU requirements](https://docs.cloud.google.com/run/docs/configuring/services/cpu)
- [Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout)
- [Ephemeral filesystem](https://docs.cloud.google.com/run/docs/container-contract#filesystem)
- [Cloud Storage volume mount limitations](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts#limitations)
- [Cloud Storage Always Free](https://cloud.google.com/storage/pricing#cloud-storage-always-free)
- [Gemini thinking and output limits](https://aistudio.google.com/docs/thinking)
- [Gemini rate limits](https://aistudio.google.com/docs/rate-limits)
- [Cloud spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)
- [Firestore free quotas and paid TTL deletes](https://firebase.google.com/docs/firestore/quotas#free-quota)
- [Firestore storage-size accounting](https://firebase.google.com/docs/firestore/storage-size)
- [Firestore TTL behavior](https://firebase.google.com/docs/firestore/ttl)
