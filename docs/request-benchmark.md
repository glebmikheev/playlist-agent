# User-request benchmark

The 15 requests in `benchmarks/cases.json` exercise artist inclusion, mixed
genres, exact counts, composer identity, exclusions and musical progression.
The requirements are checked independently of the agent's own validator.
Generated playlists, provider responses and reports remain in ignored
`reports/request_benchmark/`.

Run from the repository root:

```powershell
.venv\Scripts\python.exe benchmark.py
.venv\Scripts\python.exe benchmark.py --live
```

The first command uses saved responses and makes no model calls. The second
runs only missing cases, with a fresh conversation for each. Results are saved
after each case. A provider 429 or two consecutive unavailable responses stops
the batch. After checking the quota and waiting, `--live --retry-errors`
explicitly retries saved errors once per case; previous attempts are archived.
Successful and clarification responses are retained. The regular per-operation
503 retry policy and 180-second deadline still apply.
The batch waits six seconds between cases to reduce bursts against free-tier
rate limits. That wait is excluded from the per-run agent latency.

Open `reports/request_benchmark/comparison.html` for side-by-side playlists and
clickable Spotify links. Markdown, JSON and individual reference records are
saved alongside it. The benchmark does not modify application conversations or
their local usage counters; its API calls still consume the provider's quota.

## What the comparison measures

References use editorially selected candidates from the actual 89,740-track
catalogue. A separate whole-track subset fitter brings them into the requested
duration window, preserving the curated pool's order. This does not call the
application's retrieval, ranking, assembly or polish code. Mandatory tracks,
the pop prefix and the central hip-hop placement are explicitly arranged.
The Mozart examples select five distinct works/movements rather than five
recordings of the same piece.

These are illustrative references, not a unique gold standard or a listening
study. Curation uses known repertoire and catalogue metadata; the audio has not
been listened to. The comparison is between the complete application and an
assisted curator with catalogue lookup access. It does not establish that one
LLM is better than another. A model-only comparison would need identical
candidate pools and a separate, preferably blinded, relevance assessment.

Mechanical checks cover duration (-3/+5 minutes), count ranges, required/excluded
artists or composers, genre counts, the pop prefix, central hip-hop placement,
known IDs and duplicate IDs. A metal-family presence check detects a wholly
pop result, but does not establish that most of a playlist is metal. Broad
musical styles, regional rap identity, iconic repertoire, tension and smooth
transitions still need editorial review. Track overlap with the reference is
not a score.

Catalogue tags are noisy. For example, Electric Relaxation by A Tribe Called
Quest is labeled hardcore; its use in the reference has an explicit hip-hop
correction in the checker. Dream Theater also appears under classical, so a
classical-tag match cannot establish relevance. Duplicate recordings with
different IDs need work/recording identity checks beyond ID uniqueness.

Case 5 asks a length question. Case 4 chooses 24 tracks, exactly two by
Metallica. Case 7 treats 50 minutes as the total including country/classical
additions. Case 13 likewise treats 40 minutes as the total. These assumptions
are visible in the case definitions. Mainstream singalong pop is a musical
interpretation of the user's wording, not a filter on artists' ethnicity.

## Baseline on 4 November 2026

All 15 cases were run against Gemini 3.5 Flash-Lite without changing runtime
retrieval or selection during the batch. An initial 429 on case 10 stopped the
first batch. An explicit later retry reached the model but failed to select
valid candidates; the other five cases then completed. Both attempts are kept.

There were 11 completed playlists, three clarifications and one application
error in the retained results. Seven of 15 responses pass the limited
mechanical checks, including the correct birthday length clarification. This
is not a 47% musical-quality score: the checks do not cover every semantic
requirement.

| Case | Finding |
|---|---|
| 1 | Metallica absent from the two-hour metal playlist |
| 2 | Pop throughout; no metal-family tracks |
| 3 | Identical output to case 2; the requested mix is lost |
| 4 | Count range met, but zero Metallica tracks instead of two |
| 5 | Correct length clarification |
| 6 | Plausible fast running selection by metadata; listening still needed |
| 7 | Asks to drop country/classical counts; no playlist delivered |
| 8 | Dream Theater included in a classical selection |
| 9 | Four of five tracks are not Mozart |
| 10 | No valid ranked candidates for a phase; no playlist |
| 11 | Work exclusion incorrectly becomes an ambiguous exact-track reference |
| 12 | Much plausible study material, but lively repertoire and duplicate works remain |
| 13 | Hip-hop at the midpoint, with reggaeton in the middle block and a repeated recording/title |
| 14 | Pop prefix met; East/West Coast identity not preserved |
| 15 | Lower numerical energy, but marches and lively dances undermine the settling sequence |

The retained attempts took a median 3.87 seconds and a maximum 7.67 seconds,
with 86,096 reported tokens in total. These measurements exclude the earlier
failed 429 attempt and do not measure long-term availability. They also do not
cover an 80-track request. They are insufficient to choose public cloud quotas.
The first nine reports lack a complete source/settings fingerprint; subsequent
runs record source, case and catalogue hashes plus non-secret model settings.

`benchmarks/baseline_review.json` stores the editorial observations. Reviews
are displayed only for matching model/status/playlist hashes, so a changed
playlist cannot silently inherit the previous verdict. A matching hash is
not evidence of an independent human evaluation.

## Next implementation priorities

1. Preserve the complete musical brief even when structured fields are set.
   Several baseline requests reach ranking with an empty brief/phase description.
2. Represent mandatory artist minima/exact counts, composer identity, genre
   quotas, work exclusions and prefix counts explicitly. A similarity reference
   cannot enforce “include Metallica”. Validate them against the original request
   contract before announcing success.
3. Retrieve mandatory items first and balance mixed-genre candidate pools.
   Remove the implicit energy-0.5 preference when no energy was requested.
   Retrieval cannot rely on one global genre union for different musical phases.
4. Deduplicate recordings/works, improve classical/composer filtering and give
   relevance ranking enough context to distinguish musical character from energy.
5. Re-run the same cases into a separate report directory after changes, then
   add edit and 80-track scenarios. Inspect the two sets together before tuning
   deployment limits.

The application algorithm is deliberately unchanged in this benchmark iteration.

## Comparing the constraint fixes

Keep the original output directory intact and run a new iteration with:

```powershell
.venv\Scripts\python.exe benchmark.py --live --output reports/request_benchmark_v3
```

`--output` also controls offline report generation. Authentication/configuration
errors (400/401/403/404) now stop a batch as well as provider quota errors. Server
structured-output schemas omit numeric/length constraints to avoid Gemini grammar
complexity limits; Pydantic still enforces the complete application contract.
The initial schema-rejection attempts in the second iteration were retained,
then explicitly retried after fixing the API schema.

The final third iteration passed the mechanical checks for all 15 cases: 14
playlists and the expected birthday clarification. Case 13 initially received
429 during ranking; an explicit later retry after a pause succeeded, as did
cases 14–15. That failed attempt remains archived. The final version also passed
45 private regression tests and all ten offline tool scenarios. A saved
conversation was restored through the restarted Gradio application, retaining
clickable Spotify links.

In the retained live results, case 2 has about 6% pop playback time and case 3
about 34%, instead of the same playlist for both requests. Exact Metallica counts,
country/classical counts, the pop prefix and Mozart credit/work filtering pass.
These checks still do not establish musical quality: the mixed pop selection
does not reliably capture the intended mainstream singalong style, case 14
still lacks a convincing East/West Coast mix, and case 10's final work does not
clearly deliver the requested tension. Case 9 also includes movements/fragments
from the same Mozart work. Those are remaining semantic selection and work-
identity problems, not successes implied by the mechanical score.
