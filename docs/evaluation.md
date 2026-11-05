# Local evaluation

The first gate is a working local application. Public-demo limits are chosen
after successful generation and ranking measurements, not from API context
capacity alone.

## Repeatable runs

`python evaluate.py` exercises catalogue retrieval, constrained assembly, polish
and independent validation on ten fixed specifications. It does not call Gemini.
The two impossible cases should fail validation; they count as correct behavior
when restrictions remain intact. The report labels this scope explicitly.

`python evaluate.py --live --limit 3` sends the first three natural-language
requests through the actual graph. Use `--case` for one scenario and `--output`
to preserve a report. Each run uses the configured model and at most three
generations. Token-count preflights are reported separately. Provider errors
and missing token usage are distinct from parsing failures and zero usage.
Only successful runs contribute to successful-run latency summaries.

Record these measurements for small requests, 50/80 tracks and a 50-minute
multi-phase playlist:

| Measure | What it establishes |
| --- | --- |
| Parsed duration/count | The explicit length slots survived interpretation. |
| Independent validation | Real IDs, no duplicate tracks, duration/count, filters, locks and credited-artist caps. |
| Energy deviation by phase | Duration-weighted deviation from the inferred/requested profile; a feature proxy. |
| Stage timings | Interpretation, retrieval, ranking, assembly and polish cost. |
| Input/output/thinking tokens | Provider-reported consumption for setting later budgets. Failed calls may have unknown usage. |
| Ranking coverage | Known unique aliases ranked per phase; unknown aliases and duplicates are recorded and discarded. Unranked candidates do not enter assembly. |

The CLI checks explicit length slots automatically. Inspect genre mapping,
phase interpretation and exclusions as well; a correct length alone does not
establish correct parsing.

## Human review

Review each accepted playlist against the request. Rate semantic fit from 1 to 5,
note concrete off-theme tracks, and inspect whether the calm/energetic phases
make sense. Metadata-based relevance is not a verified account of lyrics or
audio. Do not count an unavailable-model run as a low-quality recommendation.

Then send "make the middle calmer" in the same conversation. Check that length,
excluded artists and locks survive, that the intended phase changes, and that
unaffected tracks remain. Save/reopen the conversation and verify the same
accepted result. CSV/JSON export and graph rendering must require no generation.

Compare low and minimal thinking on identical requests, saving separate reports.
Keep candidate construction and output limits fixed during the comparison.
Choose a setting from valid outputs, semantic review, latency and tokens together.
Do not tighten the output budget until both interpretation and the largest
candidate-order response complete successfully.

## Verification on 4 November 2026

- Ten offline scenarios produced the expected behavior, including both explicit
  failure cases. The tools completed each scenario in roughly 0.1–1.1 seconds
  on the development machine; these are not end-to-end LLM timings.
- Private regression checks cover edits, locks, JSON restart recovery, provider
  failure, cancellation and a depleted pool requiring a third ranking call.
- The running Gradio app restored a clearly labeled offline fixture, rendered
  six catalogue tracks and the energy chart, and exposed both exports.
- Gemini 3.1 Flash-Lite generation returned 503/504; a minimal JSON probe also
  received a provider high-demand response. An explicit switch to the available
  Free-tier model Gemini 3.5 Flash-Lite produced six tracks in 28.552 seconds,
  with two generations and 3,249 reported tokens. Metadata review found tracks
  by Aphex Twin, Mount Kimbie, Bonobo and other electronic artists; this is not
  listening-based semantic evaluation.
- Larger runs initially failed an unnecessary complete-permutation requirement.
  The ranker now accepts a relevant subset and records coverage. Assembly
  excludes unranked candidates. A 50-minute run then produced 14 tracks totaling
  2,902.85 seconds in 44.359 seconds, using 7,601 tokens and two generations.
  Hard constraints passed, but final-phase mean energy was 0.575 against a target
  of 0.3. Hard-constraint success alone does not establish a good energy profile.
- Subsequent changes separate similar phases' shortlists and optimize the actual
  energy profile during polish even after duration passes. A further run produced
  13 tracks lasting 3,026.472 seconds, with mean phase energies 0.310, 0.793 and
  0.300 against targets 0.3, 0.8 and 0.3. Hard constraints passed with no profile
  warnings. The first interpretation request returned 503; one retry succeeded.
  There were three generation attempts in 71.394 seconds. Successful responses
  reported 7,279 tokens; total consumption remains unknown because the failed
  attempt supplied no usage. This is a single live check after several changes,
  not a controlled comparison attributing the improvement to one change.
- The updated 80-track run received provider 503 during ranking after 19.734
  seconds. Its usage is incomplete and it is not a successful 80-track benchmark.
  Thinking comparisons and broader semantic quality remain unmeasured.
- Ranking preflights measured 1,859 tokens for the six-track case, 5,519 for the
  50-minute case and 18,833 for the 80-track case. These measurements use fixed
  specifications, not successful model interpretations. Model interpretations
  can change the prompt size, so each actual call retains its own preflight.
- The Dockerfile includes the prepared catalogue. Image build/run verification
  remains pending because the local Docker Engine was not running.

Raw outputs stay in the ignored `reports/` directory. No MPD data was used.
Held-out playlist continuation, Recall@K and related retrieval metrics belong to
the separate MPD experiment and cannot validate natural-language mood matching.
