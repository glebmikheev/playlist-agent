"""Run and inspect the user-request benchmark without using agent validators."""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from benchmarks.curate import create_references
from playlist_agent.agent import run_agent
from playlist_agent.catalogue import Catalogue, normalize
from playlist_agent.gemini import GeminiClient
from playlist_agent.settings import Settings

DIRECTORY = Path("reports/request_benchmark")
CASES = Path("benchmarks/cases.json")
GENRE_OVERRIDES = {
    "0eEXcw3JLVXcRxYrVYMy68": {"hip-hop"}
}  # Electric Relaxation is mislabeled hardcore.
METAL_LABELS = {
    "metal",
    "heavy-metal",
    "death-metal",
    "black-metal",
    "metalcore",
    "grindcore",
}


def check_requirements(case, ids, catalogue, status):
    tracks = catalogue.tracks(ids)
    failures = []
    if case.get("expected_action") == "clarification":
        return {
            "passed": status == "clarification",
            "failures": []
            if status == "clarification"
            else ["Expected a length clarification"],
            "duration_s": 0,
            "track_count": len(ids),
        }
    if status != "success":
        return {
            "passed": False,
            "failures": [f"No completed playlist: {status}"],
            "duration_s": 0,
            "track_count": len(ids),
        }
    if len(set(ids)) != len(ids):
        failures.append("Duplicate track IDs")
    if len(tracks) != len(ids):
        failures.append("Unknown catalogue IDs")
    if not tracks:
        failures.append("Empty playlist")
    duration = sum(track["duration_ms"] for track in tracks) / 1000
    target = case.get("duration_s")
    if target and not target - 180 <= duration <= target + 300:
        failures.append(
            f"Duration {duration / 60:.2f} min outside {target / 60 - 3:g}–{target / 60 + 5:g}"
        )
    if "count_range" in case:
        low, high = case["count_range"]
        if not low <= len(ids) <= high:
            failures.append(f"Expected {low}–{high} tracks, got {len(ids)}")
    artists = Counter(name for track in tracks for name in set(track["artist_names"]))
    labels = {
        track["track_id"]: set(track["genres"])
        | GENRE_OVERRIDES.get(track["track_id"], set())
        for track in tracks
    }
    genres = Counter(genre for values in labels.values() for genre in values)
    if "metal" in case.get("required_styles", []) and not any(
        values & METAL_LABELS for values in labels.values()
    ):
        failures.append("No track with a metal-family label")
    for field, counts in (("artist", artists), ("genre", genres)):
        for kind in ("min", "exact"):
            for name, expected in case.get(f"{field}_{kind}", {}).items():
                actual = counts[normalize(name)]
                matches = actual >= expected if kind == "min" else actual == expected
                if not matches:
                    failures.append(
                        f"{field} {name}: expected {kind} {expected}, got {actual}"
                    )
    composer = case.get("composer")
    if composer:
        wrong = [
            track["title"]
            for track in tracks
            if normalize(composer) not in track["artist_names"]
        ]
        if wrong:
            failures.append(f"Non-{composer} tracks: {len(wrong)}")
    for pattern in case.get("exclude_title_patterns", []):
        matches = [
            track["title"]
            for track in tracks
            if re.search(pattern, track["title"], re.I)
        ]
        if matches:
            failures.append(f"Excluded work present: {matches[0]}")
    prefix = case.get("prefix_genre")
    if prefix and (
        len(tracks) < prefix["count"]
        or any(
            prefix["genre"] not in track["genres"]
            for track in tracks[: prefix["count"]]
        )
    ):
        failures.append(
            f"First {prefix['count']} tracks must have {prefix['genre']} label"
        )
    if "middle_genre" in case and tracks:
        elapsed = 0
        central = False
        for track in tracks:
            if (
                case["middle_genre"] in labels[track["track_id"]]
                and elapsed <= duration * 500 <= elapsed + track["duration_ms"]
            ):
                central = True
            elapsed += track["duration_ms"]
        if not central:
            failures.append(f"No {case['middle_genre']} track at playback midpoint")
    return {
        "passed": not failures,
        "failures": failures,
        "duration_s": round(duration, 3),
        "track_count": len(ids),
    }


def fingerprint(settings):
    paths = sorted(Path("playlist_agent").glob("*.py"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    with Path("data/catalogue.sqlite").open("rb") as stream:
        catalogue_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "source_sha256": digest.hexdigest(),
        "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
        "catalogue_sha256": catalogue_hash,
        "model": settings.model,
        "thinking_level": settings.thinking_level,
        "thinking_budget": settings.thinking_budget,
        "max_input_tokens": settings.max_input_tokens,
        "max_output_tokens": settings.max_output_tokens,
        "timeout_s": settings.timeout_s,
    }


async def live(cases, catalogue, retry_errors):
    settings = Settings.load()
    provenance = fingerprint(settings)
    directory = DIRECTORY / "gemini"
    directory.mkdir(parents=True, exist_ok=True)
    unavailable = 0
    for case in cases:
        path = directory / f"{case['id']:02d}.json"
        previous = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        )
        if previous and (previous["result"]["status"] != "error" or not retry_errors):
            continue
        client = GeminiClient(settings)
        try:
            result = await run_agent(
                case["prompt"], catalogue, client, {}, settings.timeout_s
            )
        finally:
            await client.close()
        record = {
            "case": case,
            "model": settings.model,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "provenance": provenance,
            "result": result,
        }
        if previous:
            attempts = directory / "previous_attempts"
            attempts.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            (attempts / f"{case['id']:02d}-{stamp}.json").write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(case["id"], result["status"], result["metrics"]["elapsed_s"], flush=True)
        codes = [call.get("error_code") for call in result["metrics"]["calls"]]
        unavailable = (
            unavailable + 1
            if result.get("failure_reason") == "provider_unavailable"
            else 0
        )
        if 429 in codes or unavailable >= 2:
            print(
                "Stopped on quota or repeated unavailability. Saved results are retained.",
                flush=True,
            )
            break


def report(cases, catalogue):
    references, tracks = create_references(catalogue)
    directory = DIRECTORY / "reference"
    directory.mkdir(parents=True, exist_ok=True)
    review = json.loads(
        Path("benchmarks/baseline_review.json").read_text(encoding="utf-8")
    )
    rows = []
    for case in cases:
        case_id = case["id"]
        ids = references[case_id]
        reference = {
            "case": case,
            "method": "Assistant-curated candidates + independent duration fitting; no audio listening",
            "playlist_ids": ids,
            "tracks": [tracks[key] for key in ids],
        }
        if case_id == 5:
            reference["clarification"] = (
                "How long should the birthday playlist be, or how many songs would you like?"
            )
        reference["checks"] = check_requirements(
            case, ids, catalogue, "clarification" if case_id == 5 else "success"
        )
        if not reference["checks"]["passed"]:
            raise ValueError(
                f"Reference {case_id} fails: {reference['checks']['failures']}"
            )
        (directory / f"{case_id:02d}.json").write_text(
            json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        path = DIRECTORY / "gemini" / f"{case_id:02d}.json"
        row = {
            "id": case_id,
            "prompt": case["prompt"],
            "reference": reference["checks"],
            "gemini": None,
            "editorial": None,
        }
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            result = record["result"]
            row["gemini"] = {
                "status": result["status"],
                "model": record["model"],
                **check_requirements(
                    case, result.get("playlist_ids", []), catalogue, result["status"]
                ),
                "metrics": result["metrics"],
            }
            payload = {
                "status": result["status"],
                "playlist_ids": result.get("playlist_ids", []),
            }
            checksum = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            saved_review = review["cases"].get(str(case_id))
            if (
                saved_review
                and saved_review["result_sha256"] == checksum
                and record["model"] == review["model"]
            ):
                row["editorial"] = saved_review
        rows.append(row)
    (DIRECTORY / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    measured = [row["gemini"] for row in rows if row["gemini"]]
    passed = sum(row["passed"] for row in measured)
    timings = [row["metrics"]["elapsed_s"] for row in measured]
    summary = f"{len(measured)}/{len(cases)} cases run; {passed} pass mechanical checks (including clarification)."
    if timings:
        summary += f" Median {statistics.median(timings):.2f}s, maximum {max(timings):.2f}s for retained attempts."
    lines = [
        "# Request benchmark",
        "",
        summary,
        "",
        "Mechanical checks are independent of agent validation. Genre checks use catalogue labels with one documented correction: Electric Relaxation is hip-hop, despite its hardcore label. Musical fit is reviewed separately; matching track IDs is not a quality metric.",
        "",
        review["basis"],
        "",
        "The initial 429 is preserved in gemini/previous_attempts. Retained-attempt latency is not an availability estimate. Older reports lack a source/settings fingerprint; new runs record one. No runtime retrieval or selection changes were made during this baseline.",
        "",
        "| Case | Reference tracks / minutes | Gemini status | Checks | Failures |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        ref, actual = row["reference"], row["gemini"]
        lines.append(
            f"| {row['id']} | {ref['track_count']} / {ref['duration_s'] / 60:.2f} | {actual['status'] if actual else 'not run'} | {('pass' if actual['passed'] else 'fail') if actual else '—'} | {'; '.join(actual['failures']) if actual else '—'} |"
        )
    lines.extend(["", "## Editorial review", ""])
    for row in rows:
        note = row["editorial"]
        lines.append(
            f"{row['id']}. **{note['verdict'] if note else 'pending'}**: {note['note'] if note else 'This result has not been reviewed.'}"
        )
    lines.extend(
        [
            "",
            "## Reference playlists and agent output",
            "",
            "References use editorially selected catalogue pools and independent whole-track duration fitting. They are examples, not a unique gold standard. Audio was not listened to. Mozart examples use five distinct catalogue works; the tense-ending variant closes with Symphony 25's first movement.",
            "",
        ]
    )
    for case in cases:
        lines.extend([f"### {case['id']}. {case['prompt']}", "", case["notes"], ""])
        if case["id"] == 5:
            lines.extend(
                [
                    "How long should the birthday playlist be, or how many songs would you like?",
                    "",
                ]
            )
        for i, key in enumerate(references[case["id"]], 1):
            track = tracks[key]
            lines.append(
                f"{i}. [{track['artists']} — {track['title']}](https://open.spotify.com/track/{key}) ({track['duration_ms'] / 60000:.2f} min)"
            )
        lines.append("")
        path = DIRECTORY / "gemini" / f"{case['id']:02d}.json"
        if path.exists():
            result = json.loads(path.read_text(encoding="utf-8"))["result"]
            lines.extend(["**Agent output**", "", result.get("message", ""), ""])
            for i, track in enumerate(
                catalogue.tracks(result.get("playlist_ids", [])), 1
            ):
                lines.append(
                    f"{i}. [{track['artists']} — {track['title']}](https://open.spotify.com/track/{track['track_id']}) ({track['duration_ms'] / 60000:.2f} min)"
                )
            lines.append("")
    (DIRECTORY / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    # A standalone view keeps the two playlists side by side, with real links.
    html = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Playlist benchmark</title><style>body{font:16px/1.5 system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#182334;background:#fafbfd}summary{cursor:pointer;font-weight:600}details{padding:16px;border:1px solid #dce2eb;border-radius:10px;margin:16px 0;background:white}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}a{color:#1558ad}li{margin:6px 0}small{color:#526078}@media(max-width:700px){.columns{grid-template-columns:1fr}}</style><h1>Playlist benchmark</h1>",
        f"<p>{escape(summary)}</p><p>{escape(review['basis'])}</p><p>Open a case to compare the reference with the agent. Mechanical passes do not imply musical relevance. All Spotify links open in a new tab.</p>",
    ]
    for case, row in zip(cases, rows):
        actual, note = row["gemini"], row["editorial"]
        verdict = note["verdict"] if note else "pending review"
        html.append(
            f"<details><summary>{case['id']}. {escape(case['prompt'])} — {escape(verdict)}</summary><p>{escape(case['notes'])}</p>"
        )
        if note:
            html.append(f"<p>{escape(note['note'])}</p>")
        if actual:
            html.append(
                f"<p>Mechanical checks: {'pass' if actual['passed'] else 'fail'}. {escape('; '.join(actual['failures']))} Duration: {actual['duration_s'] / 60:.2f} min. Time: {actual['metrics']['elapsed_s']:.2f}s.</p>"
            )
        html.append("<div class='columns'>")
        path = DIRECTORY / "gemini" / f"{case['id']:02d}.json"
        result = (
            json.loads(path.read_text(encoding="utf-8"))["result"]
            if path.exists()
            else None
        )
        for title, ids in (
            ("Curated reference", references[case["id"]]),
            ("Agent", result.get("playlist_ids", []) if result else []),
        ):
            html.append(f"<section><h3>{title}</h3><ol>")
            for track in catalogue.tracks(ids):
                html.append(
                    f"<li><a href='https://open.spotify.com/track/{escape(track['track_id'], quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(track['artists'])} — {escape(track['title'])}</a> <small>{track['duration_ms'] / 60000:.2f} min</small></li>"
                )
            html.append("</ol>")
            if title == "Agent" and result:
                html.append(f"<p>{escape(result.get('message', ''))}</p>")
            if title == "Curated reference" and case["id"] == 5:
                html.append(
                    "<p>How long should the birthday playlist be, or how many songs would you like?</p>"
                )
            html.append("</section>")
        html.append("</div></details>")
    html.append("</html>")
    (DIRECTORY / "comparison.html").write_text("\n".join(html), encoding="utf-8")
    for row in rows:
        print(
            row["id"],
            row["gemini"]
            and {key: row["gemini"][key] for key in ("status", "passed", "failures")},
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Use Gemini for cases with no saved result"
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Explicitly retry saved errors once, retaining their previous reports",
    )
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    catalogue = Catalogue("data/catalogue.sqlite")
    if args.live:
        asyncio.run(live(cases, catalogue, args.retry_errors))
    report(cases, catalogue)
