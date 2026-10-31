# Playlist Agent

Build and revise a playlist from a natural-language request. The agent interprets
the request, retrieves real tracks, ranks their relevance with an LLM, then fits
the playlist to the requested duration and constraints.

The application is still at the design stage. Catalogue EDA is available.
Development proceeds in three stages:

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

## Request parameters

Times use seconds internally. Optional musical preferences remain unset unless
supplied or shown as an inference. A create request needs a duration or track
count; an edit can inherit them from the current playlist.

| Field | Type / values | Meaning |
| --- | --- | --- |
| `action` | `create`, `edit`, `clarify` | Create a playlist, change it, or ask a necessary question. |
| `edit_scope` | whole playlist, phase ID or track references | Identify the part affected by an edit. |
| `length.target_duration_s` | positive integer or `null` | Target playback duration. |
| `length.tolerance_s` | non-negative integer | Allowed duration error; default 180 seconds, shown to the user. |
| `length.target_track_count` | positive integer or `null` | Requested number of tracks. If duration and count are both supplied, retain both. |
| `semantic_brief` | string | Activity, mood and stylistic intent for LLM relevance ranking. |
| `genres.preferred` | list of labels | Soft genre preferences. |
| `genres.allowed` | list of labels or `null` | Explicit "only" restriction; any label in the list may match. |
| `genres.excluded` | list of labels | Genres to exclude. |
| `artists.reference` | list of names | Style/similarity references, such as "in the style of Bonobo". |
| `artists.only` | list of names or `null` | Explicit restriction to these artists. |
| `artists.excluded` | list of names | Artists to exclude, including credited collaborators. |
| `phases` | one to three phase objects | Each has a stable ID, duration and description. Optional targets: energy/valence in [0,1], BPM range and vocal/instrumental preference. |
| `diversity.preference` | low, balanced or high | Soft preference for artist variety; default balanced. |
| `diversity.max_tracks_per_artist` | positive integer or `null` | An explicit hard cap. "No repeated artists" means 1. |
| `content.explicit` | allow or exclude | Filter using the catalogue's explicit flag; default allow. |
| `tracks.keep_refs` | list of track references | Keep these tracks present. Their positions may change. |
| `tracks.exclude_refs` | list of track references | Remove or exclude these tracks. |
| `assumptions` | list of field/value/origin records | Distinguish inferred values from application defaults. |
| `unverifiable_requests` | list of requirements | Requests unsupported by available metadata, such as verified lyric language. |
| `clarification_question` | string or `null` | A question needed to resolve a missing or conflicting requirement. |

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

`interpret → retrieve → rank_candidates → assemble → validate → render`

The normal path calls an LLM for interpretation and bounded relevance ranking.
Python assembles the playlist and checks duration, exclusions, artist caps and
locks. Polish corrects violations and returns to validation, with at most two
attempts. A failing candidate does not replace the accepted playlist.
Viewing a saved playlist, rendering its graph and exporting it reuse the result
without another model call.

Candidate retrieval balances artists before ranking. Ordinary requests use a
soft repeat penalty; explicit caps remain hard. Refill the candidate pool before
reporting a shortage, without silently changing user restrictions.

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

## Remaining implementation

Start with the local catalogue, Gemini integration, graph and UI. Use a small,
quota-aware local evaluation before setting cloud resource and token budgets.
Cloud deployment follows those measurements. The [design](playlist-agent-mvp-design.md)
records the workflow, and [cloud notes](docs/cloud-demo.md) record deferred
deployment choices.
