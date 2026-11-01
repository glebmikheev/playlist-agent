# Request contract

The Pydantic models in `playlist_agent/models.py` define the implemented contract.
Gemini interprets the request into typed intent and preferences; code resolves references against the
catalogue, applies edits, validates parameters and checks the resulting playlist.
Its response is an `Interpretation`, containing a partial `update`. Applying that
update produces the accepted `PlaylistSpec`. The table uses paths relative to
`update` for musical fields.

## Fields to interpret

| Field | Meaning and interpretation |
| --- | --- |
| `action`, `edit_scope`, `replace_phases` | `create`, `edit` or `clarify`. Scope is `all` or an existing phase ID. Phase edits merge by stable ID; set `replace_phases` to replace or remove the phase structure. |
| `length.target_duration_s`, `length.tolerance_s`, `length.target_track_count` | Requested duration and/or track count. Internal time units are seconds. Duration tolerance defaults to 180 seconds and is shown as a default. Ask if neither duration nor count can be obtained from the request/current spec. |
| `semantic_brief` | Preserve activity, mood, imagery and stylistic intent in words, for example "electronic music for a rainy evening walk". This text informs LLM relevance ranking. |
| `genres.preferred`, `genres.allowed`, `genres.excluded` | Ordinary genre requests are preferences. "Only" creates an allowed set, with OR semantics inside that set. Exclusions are hard constraints. Map broad terms to supported catalogue labels and show material inferred mappings. |
| `artists.reference`, `artists.excluded`, `artists.only` | Reference artists guide musical similarity and do not restrict the pool to those artists. `only` is populated only for an explicit restriction. Resolve names with code and ask about ambiguous identities when needed. |
| `phases` | Up to three stable phase IDs, durations and semantic descriptions. Optional energy/valence targets in [0,1], BPM ranges and vocal/instrumental preferences. Inferred feature targets are soft proxies. Explicit strict numeric feature ranges may be hard constraints. Phase durations must reconcile with total duration. |
| `diversity.preference`, `diversity.max_tracks_per_artist` | A qualitative diversity preference plus an optional explicit cap. "No repeated artists" means a hard cap of 1; "at most two" means 2. The ordinary default is balanced diversity with a soft repeat penalty and no invented hard cap. Count every credited artist. |
| `content.explicit` | `allow` or `exclude`, according to catalogue metadata; default `allow`. Vocal preferences use instrumentalness as a proxy, not verified lyric/audio analysis. |
| `tracks.keep_refs`, `tracks.exclude_refs` | References to tracks to retain or remove. Accept existing table selections/positions, Spotify links or names; resolve real IDs in code. The LLM must not invent IDs. Keeping a track preserves its presence, not its exact position. |
| `assumptions`, `unverifiable_requests`, `clarification_question` | Separate explicit wishes, defaults and inferred interpretations. Record requirements unsupported by the catalogue, such as verified lyric language or year when unavailable. Ask rather than silently claim that a hard unsupported condition is satisfied. |

Keep implementation parameters outside the parsed musical specification: SQL,
candidate limits, scoring weights, LLM model settings, polish counter, thread ID
and credentials belong to the application.

## Worked interpretation

Input: "50 minutes of electronic music for a walk. Start relaxed, make the middle
more energetic, and keep the final 10 minutes calm. No repeated artists."

The following is a valid interpretation using a supported catalogue label.
The first two phase durations and numeric energy targets are visible inferences,
not numbers explicitly supplied by the user.

```json
{
  "action": "create",
  "edit_scope": "all",
  "replace_phases": false,
  "update": {
    "length": {
      "target_duration_s": 3000,
      "tolerance_s": 180
    },
    "semantic_brief": "Electronic music for a walk: relaxed start, energetic middle, calm final ten minutes.",
    "genres": {"preferred": ["electronic"]},
    "phases": [
      {"id": "start", "duration_s": 600, "description": "relaxed", "energy_target": 0.3},
      {"id": "middle", "duration_s": 1800, "description": "energetic", "energy_target": 0.7},
      {"id": "ending", "duration_s": 600, "description": "calm", "energy_target": 0.2}
    ],
    "diversity": {"max_tracks_per_artist": 1}
  },
  "tracks": {"keep_refs": [], "exclude_refs": []},
  "assumptions": [
    {"field": "length.tolerance_s", "origin": "default", "value": "180 seconds"},
    {"field": "phases.start.duration_s", "origin": "inferred", "value": "600 seconds"},
    {"field": "phases.middle.duration_s", "origin": "inferred", "value": "1800 seconds"},
    {"field": "phases.energy_target", "origin": "inferred", "value": "0.3 → 0.7 → 0.2"}
  ],
  "unverifiable_requests": [],
  "clarification_question": null
}
```

With no phase request, use one phase. An edit updates only supplied fields.
Omission preserves a current value; explicit removal clears an optional
preference. List additions/removals must be represented as typed operations or
resolved against the current list. This implementation sends the resulting list
in the patch. For example, "make the middle calmer" can produce:

```json
{
  "action": "edit",
  "edit_scope": "middle",
  "update": {"phases": [{"id": "middle", "energy_target": 0.45}]}
}
```

This preserves duration, artist caps, exclusions and the other phase definitions.
If global constraints are unchanged, the workflow also retains tracks from the
unaffected phases. `tracks.keep_refs` resolves to persistent `locked_track_ids`;
`tracks.exclude_refs` resolves to `excluded_track_ids`. The model cannot set
those internal ID lists directly. Conflicting locks and exclusions need a user
decision rather than silently dropping a rule.

Durations are limited to 60–43,200 seconds, track counts to 1–100, and duration
tolerance to 0–1,800 seconds. Optional strict `hard_feature_ranges` support
`energy`, `valence`, `tempo`, `instrumentalness`, `acousticness` and `danceability`.
Code independently checks strict ranges against every phase a track overlaps.

## Relevance and diversity

Retrieve broadly under hard constraints, then produce a feature-ranked,
artist-balanced shortlist sized for the requested playlist, initially 30–180
candidates per phase. An LLM `rank` node judges semantic fit to the brief, phase descriptions and
reference artists from the supplied track/artist/genre metadata and audio-feature
values. It returns a complete order of short integer aliases per phase. Code
rejects omitted, duplicate or invented aliases, resolves real IDs and converts
the order to relative scores. Those scores are not calibrated probabilities. Unknown musical or lyrical properties
must not become factual explanations.

Python assembles the playlist using this relevance signal alongside duration,
feature targets, transitions and diversity. Allocate the candidate budget across
artists before the final selection, use soft repetition penalties in ordinary
requests, and apply a hard per-artist cap when explicitly requested. Expand or
refill a depleted candidate pool before returning a shortage. Broaden soft genre
or reference-artist preferences as appropriate, while retaining hard exclusions,
explicit "only" filters, caps and locks.

The normal path has two LLM calls: interpretation and bounded relevance ranking.
Polish first reuses scored candidates. Pool expansion may require one further
bounded ranking call within the overall attempt budget. Pool expansion occurs
at most once and is capped at 400 candidates across mutable phases. If its input
exceeds the configured token limit, generation is declined. There are no hidden
SDK retries or model fallbacks. Polish uses no LLM calls and makes local
duration adjustments; it cannot guarantee a solution to incompatible conditions.

## Conversation persistence

Gradio session memory alone is temporary. For the local MVP, save a JSON snapshot
per conversation after each completed turn. Include a schema version, current
spec, accepted playlist IDs, messages and pending clarification. Write a temporary
file in the same directory and replace the snapshot; serialize writes within a
conversation. The UI retains the conversation ID or lets the user reopen a saved
conversation. Reset transient per-request fields explicitly. This restores
completed turns; resuming within a graph step can use a checkpointer later.
Cross-conversation preference learning is separate work.

## Spotify connection

Add Spotify authorization and playlist saving at the end of implementation.
The user connects through Spotify's authorization page (Authorization Code with
PKCE), then the application receives access/refresh tokens through its callback.
A user-supplied `.spotify-token` file is not part of the normal flow. Register the
developer application and redirect URI, request only scopes needed for the
selected feature, and store tokens separately from conversation snapshots and prompts.

Potential later features include catalogue search/metadata, reading the user's
own accessible playlists, reading authorized top/recently played items to seed
preferences, and creating/updating the user's playlists. Actual endpoint access
depends on app mode and granted scopes. The app's available catalogue remains
the source of audio features for the MVP.

References:

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Spotify Authorization Code with PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify top items](https://developer.spotify.com/documentation/web-api/reference/get-users-top-artists-and-tracks)
- [Spotify recently played](https://developer.spotify.com/documentation/web-api/reference/get-recently-played)
- [Spotify development-mode API changes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)
