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
| Complete alias permutations | Ranking output is usable without fabricated or omitted candidates. |

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
- Gemini model metadata and token counting worked. Generation returned 503/504;
  a minimal JSON probe also received a provider high-demand response. Successful
  live latency, thinking comparisons and semantic quality are still unmeasured.
- Ranking preflights measured 1,859 tokens for the six-track case, 5,519 for the
  50-minute case and 18,833 for the 80-track case. These measurements use fixed
  specifications, not successful model interpretations. Model interpretations
  can change the prompt size, so each actual call retains its own preflight.

Raw outputs stay in the ignored `reports/` directory. No MPD data was used.
Held-out playlist continuation, Recall@K and related retrieval metrics belong to
the separate MPD experiment and cannot validate natural-language mood matching.
