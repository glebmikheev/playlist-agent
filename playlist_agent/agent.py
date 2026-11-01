import asyncio
import time
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .gemini import ModelError
from .models import Phase, PlaylistSpec, apply_interpretation, phase_schedule
from .selection import (
    build_playlist,
    diverse_shortlist,
    feature_cost,
    polish_playlist,
    track_allowed,
    validate_playlist,
)


class State(TypedDict, total=False):
    message: str
    current_spec: PlaylistSpec | None
    previous_ids: list[str]
    messages: list[dict]
    spec: PlaylistSpec
    pools: dict
    scores: dict
    fixed: dict
    frozen_phases: list[str]
    protected_ids: list[str]
    ids: list[str]
    validation: dict
    polish_attempts: int
    refilled: bool
    refill_available: bool
    clarification: str | None


def make_graph(catalogue, model, events):
    async def interpret(state):
        started = time.perf_counter()
        parsed = await model.interpret(
            state["message"],
            state["current_spec"],
            state["messages"],
            catalogue.vocabulary(),
        )
        if parsed.action == "clarify" or parsed.clarification_question:
            return {
                "clarification": parsed.clarification_question
                or "Уточни длительность или число треков."
            }
        if parsed.unverifiable_requests:
            return {
                "clarification": "В каталоге нельзя проверить: "
                + "; ".join(parsed.unverifiable_requests)
                + ". Можно продолжить без обязательного выполнения этих условий?"
            }
        try:
            spec = apply_interpretation(state["current_spec"], parsed)
            if (
                spec.length.target_duration_s is None
                and spec.length.target_track_count is None
            ):
                return {
                    "clarification": "На сколько минут или треков собрать плейлист?",
                    "spec": spec,
                }
            spec.locked_track_ids = list(
                dict.fromkeys(
                    [
                        *spec.locked_track_ids,
                        *catalogue.resolve(
                            parsed.tracks.keep_refs, state["previous_ids"]
                        ),
                    ]
                )
            )
            spec.excluded_track_ids = list(
                dict.fromkeys(
                    [
                        *spec.excluded_track_ids,
                        *catalogue.resolve(
                            parsed.tracks.exclude_refs, state["previous_ids"]
                        ),
                    ]
                )
            )
            if not spec.phases:
                spec.phases = [Phase(id="all", description=spec.semantic_brief)]
            spec = PlaylistSpec.model_validate(spec.model_dump())
        except ValueError as exc:
            return {
                "clarification": "Нужно согласовать параметры: "
                + str(exc).split("\n")[0]
            }

        locked = catalogue.tracks(spec.locked_track_ids)
        if len(locked) != len(spec.locked_track_ids) or any(
            not track_allowed(track, spec) for track in locked
        ):
            return {
                "clarification": "Закреплённые треки отсутствуют в каталоге или конфликтуют с новыми ограничениями. Какие треки разблокировать?"
            }
        counts = Counter(name for track in locked for name in track["artist_names"])
        cap = spec.diversity.max_tracks_per_artist
        if cap is not None and any(count > cap for count in counts.values()):
            return {
                "clarification": "Закреплённые треки уже превышают лимит исполнителя. Разблокировать часть треков или изменить лимит?"
            }

        fixed = {phase.id: [] for phase in spec.phases}
        frozen, protected = [], []
        current = state["current_spec"]
        exclude = {"phases", "semantic_brief", "assumptions", "locked_track_ids"}
        if (
            current
            and parsed.action == "edit"
            and parsed.edit_scope in fixed
            and current.model_dump(exclude=exclude) == spec.model_dump(exclude=exclude)
        ):
            previous = catalogue.tracks(state["previous_ids"])
            old_schedule = phase_schedule(
                current, sum(track["duration_ms"] for track in previous)
            )
            elapsed = 0
            for track in previous:
                midpoint = elapsed + track["duration_ms"] / 2
                elapsed += track["duration_ms"]
                phase_id = next(
                    (
                        phase.id
                        for phase, start, end in old_schedule
                        if start <= midpoint < end
                    ),
                    old_schedule[-1][0].id,
                )
                if phase_id in fixed and phase_id != parsed.edit_scope:
                    fixed[phase_id].append(track)
                    protected.append(track["track_id"])
            frozen = [phase_id for phase_id in fixed if phase_id != parsed.edit_scope]
        placed = {track["track_id"] for group in fixed.values() for track in group}
        for track in locked:
            if track["track_id"] not in placed:
                eligible = [
                    phase for phase in spec.phases if track_allowed(track, spec, phase)
                ]
                if not eligible:
                    return {
                        "clarification": f"Закреплённый трек {track['title']} не подходит ни под одну обязательную фазу."
                    }
                phase = min(eligible, key=lambda phase: feature_cost(track, phase))
                fixed[phase.id].append(track)
        events.append(
            {
                "stage": "interpret",
                "seconds": round(time.perf_counter() - started, 3),
                "scope": parsed.edit_scope,
            }
        )
        return {
            "spec": spec,
            "fixed": fixed,
            "frozen_phases": frozen,
            "protected_ids": protected,
        }

    def retrieve(state):
        started = time.perf_counter()
        spec = state["spec"]
        schedule = phase_schedule(spec)
        total = schedule[-1][2]
        expected = spec.length.target_track_count or max(1, round(total / 210000))
        pools = {}
        for phase, start, end in schedule:
            desired = max(1, round(expected * (end - start) / total))
            size = min(180, max(30, desired * 4 + 8))
            candidates = catalogue.search(spec, phase, min(8000, max(2500, size * 25)))
            candidates.sort(key=lambda track: feature_cost(track, phase))
            pools[phase.id] = diverse_shortlist(candidates, size)
            if not pools[phase.id] and phase.id not in state["frozen_phases"]:
                return {
                    "clarification": f"Для фазы {phase.id} не найдено треков с обязательными фильтрами. Какие ограничения можно изменить?"
                }
        events.append(
            {
                "stage": "retrieve",
                "seconds": round(time.perf_counter() - started, 3),
                "candidates": {key: len(value) for key, value in pools.items()},
            }
        )
        return {"pools": pools}

    async def rank(state):
        started = time.perf_counter()
        phases = [
            phase
            for phase in state["spec"].phases
            if phase.id not in state["frozen_phases"]
        ]
        ranking_spec = state["spec"].model_copy(update={"phases": phases})
        scores = await model.rank(ranking_spec, state["pools"])
        events.append(
            {"stage": "rank", "seconds": round(time.perf_counter() - started, 3)}
        )
        return {"scores": scores}

    def assemble(state):
        started = time.perf_counter()
        ids = build_playlist(
            state["spec"],
            state["pools"],
            state["scores"],
            state["fixed"],
            state["frozen_phases"],
        )
        events.append(
            {
                "stage": "assemble",
                "seconds": round(time.perf_counter() - started, 3),
                "tracks": len(ids),
            }
        )
        return {"ids": ids}

    def validate(state):
        started = time.perf_counter()
        report = validate_playlist(state["spec"], state["ids"], catalogue)
        events.append(
            {
                "stage": "validate",
                "seconds": round(time.perf_counter() - started, 3),
                "passed": report["passed"],
            }
        )
        return {"validation": report}

    def polish(state):
        started = time.perf_counter()
        ids = polish_playlist(
            state["spec"],
            state["ids"],
            state["pools"],
            catalogue,
            state["protected_ids"],
            state["frozen_phases"],
        )
        events.append(
            {"stage": "polish", "seconds": round(time.perf_counter() - started, 3)}
        )
        return {"ids": ids, "polish_attempts": state["polish_attempts"] + 1}

    def refill(state):
        started = time.perf_counter()
        phases = [
            phase
            for phase in state["spec"].phases
            if phase.id not in state["frozen_phases"]
        ]
        sizes = {
            phase.id: min(250, len(state["pools"][phase.id]) * 2) for phase in phases
        }
        total = sum(sizes.values())
        if total > 400:
            sizes = {key: int(size * 400 / total) for key, size in sizes.items()}
        pools = dict(state["pools"])
        changed = False
        for phase in phases:
            old = pools[phase.id]
            if sizes[phase.id] <= len(old):
                continue
            candidates = catalogue.search(state["spec"], phase, 8000)
            candidates.sort(key=lambda track: feature_cost(track, phase))
            expanded = diverse_shortlist(candidates, sizes[phase.id])
            if len(expanded) > len(old):
                pools[phase.id] = expanded
                changed = True
        events.append(
            {
                "stage": "refill",
                "seconds": round(time.perf_counter() - started, 3),
                "expanded": changed,
            }
        )
        return {
            "pools": pools,
            "refilled": True,
            "refill_available": changed,
            "polish_attempts": 0,
        }

    def after_validation(state):
        if state["validation"]["passed"]:
            return END
        if state["polish_attempts"] < 2:
            return "polish"
        if not state["refilled"] and len(model.calls) < 3:
            return "refill"
        return END

    graph = StateGraph(State)
    for name, node in [
        ("interpret", interpret),
        ("retrieve", retrieve),
        ("rank", rank),
        ("assemble", assemble),
        ("validate", validate),
        ("polish", polish),
        ("refill", refill),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "interpret")
    graph.add_conditional_edges(
        "interpret", lambda state: END if state.get("clarification") else "retrieve"
    )
    graph.add_conditional_edges(
        "retrieve", lambda state: END if state.get("clarification") else "rank"
    )
    graph.add_edge("rank", "assemble")
    graph.add_edge("assemble", "validate")
    graph.add_conditional_edges(
        "validate",
        after_validation,
    )
    graph.add_edge("polish", "validate")
    graph.add_conditional_edges(
        "refill", lambda state: "rank" if state["refill_available"] else END
    )
    return graph.compile()


async def run_agent(message, catalogue, model, snapshot, timeout_s=180):
    if not message.strip() or len(message) > 2000:
        raise ValueError("Сообщение должно содержать от 1 до 2000 символов.")
    started, events = time.perf_counter(), []
    current_data = snapshot.get("draft_spec") or snapshot.get("spec")
    current = PlaylistSpec.model_validate(current_data) if current_data else None
    state = {
        "message": message,
        "current_spec": current,
        "previous_ids": snapshot.get("playlist_ids", []),
        "messages": snapshot.get("messages", []),
        "polish_attempts": 0,
        "refilled": False,
        "clarification": None,
    }
    try:
        async with asyncio.timeout(timeout_s):
            result = await make_graph(catalogue, model, events).ainvoke(
                state, {"recursion_limit": 20}
            )
        if result.get("clarification"):
            response = {"status": "clarification", "message": result["clarification"]}
            if result.get("spec"):
                response["spec"] = result["spec"].model_dump(mode="json")
        else:
            valid = result["validation"]["passed"]
            response = {
                "status": "success" if valid else "failed",
                "spec": result["spec"].model_dump(mode="json"),
                "playlist_ids": result["ids"],
                "validation": result["validation"],
                "message": "Плейлист готов."
                if valid
                else "Не удалось выполнить все ограничения: "
                + " ".join(result["validation"]["errors"]),
            }
    except TimeoutError:
        response = {
            "status": "error",
            "message": "Время выполнения истекло. Предыдущий плейлист сохранён; автоматического повтора нет.",
        }
    except (ModelError, ValueError) as exc:
        response = {"status": "error", "message": str(exc)}
    response["metrics"] = {
        "elapsed_s": round(time.perf_counter() - started, 3),
        "events": events,
        "llm_calls": len(model.calls),
        "token_count_calls": model.token_count_calls,
        "calls": model.calls,
        "total_tokens": sum(call["total_tokens"] for call in model.calls)
        if all("total_tokens" in call for call in model.calls)
        else None,
    }
    return response
