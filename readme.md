# Playlist Agent

Build and revise a playlist from a natural-language request. The agent interprets
the request, retrieves real tracks, ranks their relevance with an LLM, then fits
the playlist to the requested duration and constraints.

The local application includes a SQLite catalogue, Gemini interpretation and
ranking, a LangGraph workflow, JSON conversation storage, a Gradio interface,
energy plots and CSV/JSON export. The interface and generated explanations use
English only; original track and artist names are preserved. Development proceeds
in three stages:

1. Build a local Gemini-powered MVP: generation, edits, validation, polish,
   plotting and export. It needs a Gemini API key, without deploying Cloud Run,
   Firestore or other application infrastructure in GCP.
2. Measure local latency, token usage, parsing/constraint correctness and semantic
   relevance, including 50/80-track requests. Tune reasoning and response budgets.
3. Build a separate GCP application around the measured workflow. Its quotas,
   durable storage, credentials and deployment are isolated from the local app.

Both applications use the same playlist-building package. The shared code and
local entry point do not import the cloud application or its infrastructure SDKs.
Spotify OAuth and saving come at the end of implementation. MPD continuation
evaluation remains a separate recommendation experiment.

## Run locally

Use Python 3.12. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe prepare_data.py --download
Copy-Item .env.example .env
```

If `.env` already exists, keep it. Set `GEMINI_API_KEY` and `GEMINI_MODEL` in that
file, then start the app:

```powershell
.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:7860. The default model is `gemini-3.5-flash-lite`; the
configured model is used without automatic fallback. The local app listens only
on loopback. The key stays on the server and is excluded from saved sessions,
exports and version control. Use a Gemini project on Free tier for free API use;
application limits cannot change an account's provider billing settings.

The prepared database contains 89,740 tracks and occupies about 44.5 MiB.
`prepare_data.py` checks the pinned CSV checksum before importing. Downloading
and preparing data are separate from application startup.

For a local container, prepare the database first, then build and run:

```powershell
docker build -t playlist-agent:local .
docker run --rm -p 127.0.0.1:7860:8080 --env-file .env -v playlist-agent-sessions:/app/data/sessions -v playlist-agent-security:/app/data/security playlist-agent:local
```

The image includes the read-only SQLite catalogue. The build context excludes
secrets, raw data, notebooks, reports and private checks. Pass the key at runtime.
The named volume retains local conversations. Public GCP hosting still requires
the separate application with durable quota admission.

Create a playlist, inspect its interpretation and energy profile, then describe
an edit. Use the track selector to retain particular tracks. Completed turns
are saved under `data/sessions/`; choose a saved dialogue to reopen it after
restart. This local UI shares its saved-dialogue list with whoever can access
the process; the public deployment needs its own access and session handling.

## Request parameters

Times use seconds internally. Optional musical preferences remain unset unless
supplied or shown as an inference. A create request needs a duration or track
count; an edit can inherit them from the current playlist.
The model returns an `Interpretation`: `action`, `edit_scope`, a partial `update`,
track references, assumptions and any clarification. The musical fields below
are inside `update`; the accepted `PlaylistSpec` stores their merged values.

| Field | Type / values | Meaning |
| --- | --- | --- |
| `action` | `create`, `edit`, `clarify`, `reject` | Create a playlist, change it, ask a necessary question, or reject blocked content. |
| `edit_scope` | `all` or an existing phase ID | Identify the part affected by an edit. Track references are separate. |
| `length.target_duration_s` | integer 60–43,200 or `null` | Target playback duration. |
| `length.tolerance_before_s`, `length.tolerance_after_s` | integers 0–1,800 | Allowed shortfall and overrun; defaults 180 and 300 seconds, shown as −3/+5 minutes. Explicit user tolerances override these defaults. |
| `length.target_track_count` | integer 1–100 or `null` | Requested number of tracks. If duration and count are both supplied, retain both. |
| `semantic_brief` | string | Activity, mood and stylistic intent for LLM relevance ranking. |
| `genres.preferred` | list of labels | Soft genre preferences. |
| `genres.allowed` | list of labels or `null` | Explicit "only" restriction; any label in the list may match. |
| `genres.excluded` | list of labels | Genres to exclude. |
| `artists.reference` | list of names | Style/similarity references, such as "in the style of Bonobo". |
| `artists.only` | list of names or `null` | Explicit restriction to these artists. |
| `artists.excluded` | list of names | Artists to exclude, including credited collaborators. |
| `phases` | up to three phase objects | Each has a stable ID, duration and description. Optional targets: energy/valence in [0,1], BPM range and vocal/instrumental preference. With no phases, code adds one. |
| `diversity.preference` | low, balanced or high | Soft preference for artist variety; default balanced. |
| `diversity.max_tracks_per_artist` | positive integer or `null` | An explicit hard cap. "No repeated artists" means 1. |
| `content.explicit` | allow or exclude | Filter using the catalogue's explicit flag; default allow. |
| `tracks.keep_refs` | list of track references | Keep these tracks present. Their positions may change. |
| `tracks.exclude_refs` | list of track references | Remove or exclude these tracks. |
| `assumptions` | list of field/value/origin records | Distinguish inferred values from application defaults. |
| `unverifiable_requests` | list of requirements | Requests unsupported by available metadata, such as verified lyric language. |
| `clarification_question` | string or `null` | A question needed to resolve a missing or conflicting requirement. |
| `moderation` | required decision/category/reason | Gemini's safety assessment of the current request, returned with interpretation. A blocked request ends before retrieval. |

"Electronic music" is a preference; "only techno" is a restriction. "Like Bonobo"
guides similarity; "only Bonobo" limits the artists. Numeric targets inferred from
words such as "calm" remain visible proxies, not measured user requirements.

Track references can be table selections, positions, Spotify links or names.
Code resolves catalogue IDs. Edits preserve omitted fields; an explicit removal
clears an optional preference. Phase durations must agree with the total.

The [request contract](docs/request-contract.md) includes a worked JSON example.
SQL, ranking weights, candidate limits, model settings, session IDs and credentials
are application settings rather than parameters extracted from musical requests.

## Workflow and persistence

`interpret → retrieve → rank → assemble → validate`

The normal path calls an LLM for interpretation and bounded relevance ranking.
Python assembles the playlist and checks duration, exclusions, artist caps and
locks. Polish makes bounded local duration/profile adjustments and returns to validation,
with at most two attempts per candidate pool. If validation still fails, the
graph may expand the pool once and rank again. It uses at most three logical
model operations per run, with no SDK retries. A 503 can receive two explicit
retries, after 2–4 and 4–8 seconds. This permits at most nine generation attempts
within the unchanged agent deadline. Every physical attempt remains in metrics.
Quota errors and invalid requests are not retried. The final ranking response still goes through assembly,
polish and validation; the limit prevents further model calls, not completion
of local work. A failing candidate does not replace the accepted
playlist. A phase edit retains the other phases' tracks when global constraints
are unchanged. Locks preserve presence; they do not fix a track's position.
Viewing a saved playlist, rendering its graph and exporting it reuse the result
without another model call.

Each generation has a token-count preflight. Initial local settings are 24,000
input tokens, 4,096 output tokens, low thinking for Gemini 3.x and a 180-second
agent deadline. Numeric `GEMINI_THINKING_BUDGET` applies to Gemini 2.5;
`GEMINI_THINKING_LEVEL` applies to 3.x. Requests are limited to 2,000 characters.
These are measurement settings, not the final public-demo budget. API failures
return a message and preserve the last accepted playlist. Missing provider usage
is recorded as unknown rather than zero.
Unavailable runs do not increment the local user's `runs_used` counter. Physical
provider attempts are still counted; user allowance refunds do not refund
Google quota or the future public application's global resource budget.
See [provider errors](docs/provider-errors.md) for the retry policy and diagnostic evidence.

Moderation shares the first interpretation call. Rejected requests are not
retrieved or ranked, and they stay out of later model context. A private audit
database stores the classification, submitted content, connection IP and
sanitized HTTP metadata. The UI announces this and claims logging only after
the audit write succeeds. See [security notes](docs/security.md) for redaction,
retention, SQL checks and the limits of model-based moderation.

Polish evaluates the actual time occupied by each track in every phase. It can
improve energy fit even when total duration already passes, while retaining hard
constraints, locks and frozen phases. A phase energy deviation over 0.15 produces
a visible warning, not a new hard user constraint.

Candidate retrieval balances artists before ranking. Ordinary requests use a
soft repeat penalty; explicit caps remain hard. Refill the candidate pool before
reporting a shortage, without silently changing user restrictions.
Similar phases receive different shortlists when the catalogue is broad enough,
so the opening does not consume all the ending's candidates.
The model may rank a relevant subset instead of every retrieved candidate.
Unknown aliases are discarded, duplicate aliases are deduplicated, and coverage
is reported in metrics. Assembly uses only ranked candidates, retained tracks
and frozen phases; omitted candidates are not silently treated as LLM-approved.

For the local MVP, save a JSON snapshot per conversation after each completed
turn. Include a schema version, current specification, accepted playlist IDs,
messages and any pending question. Write a temporary file in the same directory,
then replace the snapshot. Keep a stable conversation ID and serialize writes
within a conversation. This restores completed turns after restart; resuming
inside a graph step would require a checkpointer later. Credentials stay outside
snapshots and prompts.

The public Cloud Run deployment needs external durable state for quotas and
saved conversations. Its scale-to-zero behavior, operating windows and proposed
resource limits are described in [cloud demo notes](docs/cloud-demo.md).

## Data and EDA

The selected [HF catalogue](https://huggingface.co/datasets/maharshipandya/spotify-tracks-dataset)
is pinned to revision `635b034f69257814eff850a5c2b3346fe458134f`.
It has 114,000 rows, 89,741 distinct track IDs and 89,740 tracks with the fields
needed for the duration/energy/genre/artist/title demo. Merge genre labels across
repeated IDs and split artist credits on semicolons.

The [original Zenodo source](https://zenodo.org/records/11453410) has 899,702 IDs,
but only 810 tracks meet those same requirements. Most records lack duration or
artist names. Full source files stay in the ignored `data/raw/` directory:

- `tracks.csv`: original Zenodo file; published MD5 `c32dffb8f9a62fec8c5892b464d7ea42`.
- `spotify_tracks_hf.csv`: revision-pinned HF file; SHA256 `b202fa49909b2d5cef71a04b1d21243cfeb36414535f2ca9272aa646721177bd`.

[Download the pinned HF CSV](https://huggingface.co/datasets/maharshipandya/spotify-tracks-dataset/resolve/635b034f69257814eff850a5c2b3346fe458134f/dataset.csv).
Rename it to `spotify_tracks_hf.csv` locally.

Use Python 3.12. Install the EDA dependencies and execute the revised notebook
from the repository root:

```powershell
python -m pip install -r requirements-eda.txt
python -m jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=300 notebooks/catalogue_eda.ipynb
```

[Catalogue EDA](notebooks/catalogue_eda.ipynb) covers source checks,
missing/invalid values, duplicate conflicts, coverage and feature plots.
It generates findings, summary JSON and figures locally in `reports/eda_v2/`.
That directory is ignored and is not required as input to the notebook.

## Local evaluation

Run checks against the real catalogue without model calls:

```powershell
.venv\Scripts\python.exe evaluate.py
```

Ten scenarios cover duration, 50/80-track selection, genre/content filters, artist
caps and impossible requests. These checks measure the selection tools, not
natural-language understanding or recommendation quality. Reports are local
under the ignored `reports/` directory.

An explicit live batch consumes Gemini quota:

```powershell
.venv\Scripts\python.exe evaluate.py --live --case six_tracks
.venv\Scripts\python.exe evaluate.py --live --limit 3
```

The three-case batch includes a small request, a 50-minute profile and 80 tracks.
It records parsed length, constraint results, stage timings and provider token
usage. Compare `--thinking-level minimal` with `--thinking-level low` on the same
cases; use `--output` to retain separate reports. Review musical relevance by
hand as described in [evaluation notes](docs/evaluation.md).

Current verification: the ten offline scenarios pass. Gemini 3.1 Flash-Lite
generation attempts returned provider 503/504 errors. An explicit switch to
Gemini 3.5 Flash-Lite produced a six-track playlist in 28.6 seconds using two
generations and 3,249 reported tokens. After relaxing an overly strict complete-
permutation check, a 50-minute request produced 14 tracks in 44.4 seconds using
7,601 tokens. It passed hard constraints, but the ending's energy profile was
too energetic. After separating similar phases' shortlists and updating polish,
a further run produced 13 tracks lasting 50:26, with final-phase mean energy
0.300 against a target of 0.3. It recovered from an initial 503 within three
generation attempts and finished in 71.4 seconds. This is one successful check,
not an availability or musical-quality benchmark. The
80-track run received another provider 503. Musical quality and thinking-setting
comparisons still need review. A fixed-specification
probe measured about 18,833 input tokens for the 348-candidate, 80-track ranking
prompt; this preflight is separate from successful recommendation measurements.

## Next milestones

Complete live evaluation with the selected model, then tune
thinking, prompt/pool size and output allowance. Choose cloud quotas from those
measurements and build the independent GCP entry point. Spotify connection and
playlist saving follow the core demo. The [design](playlist-agent-mvp-design.md)
records the original scope; [cloud notes](docs/cloud-demo.md) record deferred
deployment choices. MPD remains a later continuation experiment.
