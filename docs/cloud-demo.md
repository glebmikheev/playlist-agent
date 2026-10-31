# Public demo deployment

Design notes checked on 4 November 2026. Infrastructure has not been deployed.
The demo uses Gemini Developer API and a Cloud Run service. Actual model access
and RPM/TPM/RPD limits must be checked in the selected AI Studio project.

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

Build the catalogue once. Measure SQLite size, total image size and cold-start
behavior before choosing catalogue placement. A packaged read-only catalogue,
a file in external cloud storage, or a separate database remain deployment
options; storing SQLite in the image is not a fixed requirement. Catalogue size
must not be assumed small before measurement. Exclude source CSVs, notebooks,
reports and development/test dependencies from the runtime image.
Viewing, plotting and exporting an existing result must not call the LLM again.

Do not add periodic warm-up requests. Defer transport choices to the cloud stage
after measuring Gradio's actual idle HTTP behavior. The local MVP does not need
custom connection-management machinery, and a completed operation leaves its
result and conversation available. Waiting for Gemini inside an active HTTP
request still contributes to the request's billable duration.

## Persistence and admission

Local JSON snapshots remain sufficient for local development. The public service
needs external durable state, proposed as Firestore: quota counters, run records
and any conversations that must survive instance replacement. The catalogue is
read-only; no quota may rely on a local file or warm-process memory.

Before calling a model, atomically reserve the run's configured worst-case token
budget and check global/session/window/provider quotas. Every generation attempt
counts toward the three-call ceiling, including retries. Repeated submissions
reuse a run ID. If the outcome of a timed-out call is unknown, retain the
conservative reservation. If the quota store is unavailable, decline model work.

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
- [Gemini thinking and output limits](https://aistudio.google.com/docs/thinking)
- [Gemini rate limits](https://aistudio.google.com/docs/rate-limits)
- [Cloud spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)
