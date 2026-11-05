import argparse
import asyncio
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path

from playlist_agent.agent import run_agent
from playlist_agent.catalogue import Catalogue
from playlist_agent.gemini import GeminiClient
from playlist_agent.models import Phase, PlaylistSpec, phase_schedule
from playlist_agent.selection import (
    build_playlist,
    diverse_shortlist,
    polish_playlist,
    validate_playlist,
)
from playlist_agent.settings import ROOT, Settings

SCENARIOS = [
    {
        "id": "six_tracks",
        "prompt": "Подбери 6 спокойных электронных треков, без повторов исполнителей.",
        "spec": {
            "length": {"target_track_count": 6},
            "genres": {"preferred": ["electronic"]},
            "diversity": {"max_tracks_per_artist": 1},
        },
    },
    {
        "id": "walk_50",
        "prompt": "50 минут электроники для прогулки: 10 минут спокойно, 30 минут энергично, 10 минут спокойно. Без повторов исполнителей.",
        "spec": {
            "length": {"target_duration_s": 3000},
            "genres": {"preferred": ["electronic"]},
            "diversity": {"max_tracks_per_artist": 1},
            "phases": [
                {"id": "start", "duration_s": 600, "energy_target": 0.3},
                {"id": "middle", "duration_s": 1800, "energy_target": 0.8},
                {"id": "end", "duration_s": 600, "energy_target": 0.2},
            ],
        },
    },
    {
        "id": "eighty_tracks",
        "prompt": "Собери 80 электронных треков: спокойное начало, энергичная середина, спокойный конец. Без повторов исполнителей.",
        "spec": {
            "length": {"target_track_count": 80},
            "genres": {"preferred": ["electronic"]},
            "diversity": {"max_tracks_per_artist": 1},
            "phases": [
                {"id": "start", "energy_target": 0.3},
                {"id": "middle", "energy_target": 0.8},
                {"id": "end", "energy_target": 0.2},
            ],
        },
    },
    {
        "id": "fifty_tracks",
        "prompt": "Подбери 50 chill треков, максимум два от одного исполнителя.",
        "spec": {
            "length": {"target_track_count": 50},
            "genres": {"preferred": ["chill"]},
            "diversity": {"max_tracks_per_artist": 2},
        },
    },
    {
        "id": "techno_only",
        "prompt": "15 треков, только techno, без explicit, не больше двух треков одного исполнителя.",
        "spec": {
            "length": {"target_track_count": 15},
            "genres": {"allowed": ["techno"]},
            "content": {"explicit": "exclude"},
            "diversity": {"max_tracks_per_artist": 2},
        },
    },
    {
        "id": "instrumental",
        "prompt": "30 минут инструментальной музыки для работы, желательно ambient.",
        "spec": {
            "length": {"target_duration_s": 1800},
            "genres": {"preferred": ["ambient"]},
            "phases": [{"id": "all", "vocals_preference": "instrumental"}],
        },
    },
    {
        "id": "exclude_genre",
        "prompt": "20 рок-треков без metal, без повторов исполнителей.",
        "spec": {
            "length": {"target_track_count": 20},
            "genres": {"preferred": ["rock"], "excluded": ["metal"]},
            "diversity": {"max_tracks_per_artist": 1},
        },
    },
    {
        "id": "short_duration",
        "prompt": "10 минут музыки с энергией около 0.5, допустимо отклонение в одну минуту.",
        "spec": {
            "length": {"target_duration_s": 600, "tolerance_s": 60},
            "phases": [{"id": "all", "energy_target": 0.5}],
        },
    },
    {
        "id": "empty_pool",
        "prompt": "10 треков только в жанре nonexistent-genre-xyz.",
        "spec": {
            "length": {"target_track_count": 10},
            "genres": {"allowed": ["nonexistent-genre-xyz"]},
        },
        "expected_failure": True,
    },
    {
        "id": "impossible_artist_cap",
        "prompt": "80 треков только исполнителя John Mayer, максимум один его трек.",
        "spec": {
            "length": {"target_track_count": 80},
            "artists": {"only": ["John Mayer"]},
            "diversity": {"max_tracks_per_artist": 1},
        },
        "expected_failure": True,
    },
]


def offline(catalogue):
    records = []
    for case in SCENARIOS:
        started_at = time.perf_counter()
        spec = PlaylistSpec.model_validate(case["spec"])
        if not spec.phases:
            spec.phases = [Phase(id="all")]

        pools = {
            phase.id: diverse_shortlist(catalogue.search(spec, phase, 4000), 350)
            for phase, _, _ in phase_schedule(spec)
        }
        ids = polish_playlist(spec, build_playlist(spec, pools, {}), pools, catalogue)
        validation = validate_playlist(spec, ids, catalogue)
        records.append(
            {
                "case": case["id"],
                "expected_behavior": validation["passed"]
                != case.get("expected_failure", False),
                "seconds": round(time.perf_counter() - started_at, 3),
                "validation": validation,
                "playlist_ids": ids,
            }
        )
    return {
        "mode": "offline_tools_only",
        "scope": "No Gemini calls; does not evaluate language parsing or semantic ranking.",
        "cases": records,
        "passed": sum(record["expected_behavior"] for record in records),
    }


async def live(catalogue, cases, settings):
    records = []
    for case in cases:
        client = GeminiClient(settings)
        try:
            result = await run_agent(
                case["prompt"], catalogue, client, {}, settings.timeout_s
            )
        finally:
            await client.close()

        expected = PlaylistSpec.model_validate(case["spec"]).length.model_dump(
            exclude_unset=True
        )
        actual = result.get("spec", {}).get("length", {})
        if result.get("spec"):
            parsing = all(actual.get(key) == value for key, value in expected.items())
        else:
            parsing = None
        records.append(
            {
                "case": case["id"],
                "prompt": case["prompt"],
                "length_parsed_correctly": parsing,
                "result": result,
            }
        )
        print(case["id"], result["status"], result["metrics"]["elapsed_s"], flush=True)

    successful = [
        record for record in records if record["result"]["status"] == "success"
    ]
    durations = [record["result"]["metrics"]["elapsed_s"] for record in successful]
    all_tokens_known = all(
        record["result"]["metrics"]["total_tokens"] is not None for record in records
    )
    return {
        "mode": "live_gemini",
        "model": settings.model,
        "thinking_level": settings.thinking_level,
        "thinking_budget": settings.thinking_budget,
        "max_output_tokens": settings.max_output_tokens,
        "cases": records,
        "successful_runs": len(successful),
        "error_runs": sum(record["result"]["status"] == "error" for record in records),
        "success_latency_median_s": statistics.median(durations) if durations else None,
        "success_latency_max_s": max(durations) if durations else None,
        "total_tokens": sum(
            record["result"]["metrics"]["total_tokens"] for record in records
        )
        if all_tokens_known
        else None,
        "scope": "Small sanity sample; relevance needs human review. No MPD benchmark.",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run catalogue sanity checks or an explicit small Gemini batch."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Uses the configured Gemini API and consumes quota.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--case", choices=[case["id"] for case in SCENARIOS])
    parser.add_argument(
        "--thinking-level", choices=["minimal", "low", "medium", "high"]
    )
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    catalogue = Catalogue(ROOT / "data/catalogue.sqlite")
    if args.live:
        settings = Settings.load()
        if args.thinking_level:
            settings = replace(settings, thinking_level=args.thinking_level)
        if args.max_output_tokens:
            settings = replace(settings, max_output_tokens=args.max_output_tokens)
        if args.case:
            selected = [case for case in SCENARIOS if case["id"] == args.case]
        else:
            selected = SCENARIOS[: args.limit]
        if not selected:
            parser.error("Select at least one case.")
        report = asyncio.run(live(catalogue, selected, settings))
    else:
        report = offline(catalogue)

    path = args.output or ROOT / "reports" / (
        "live_eval.json" if args.live else "tool_eval.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(path)


if __name__ == "__main__":
    main()
