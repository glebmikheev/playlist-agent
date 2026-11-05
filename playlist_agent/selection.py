from collections import Counter, defaultdict

from .catalogue import normalize
from .models import phase_schedule

ENERGY_DEVIATION_WARNING = 0.15


def feature_cost(track, phase):
    cost = 0.0
    for feature, target, weight in [
        ("energy", phase.energy_target, 1.5),
        ("valence", phase.valence_target, 0.5),
    ]:
        if target is not None:
            value = track[feature]
            cost += weight * (abs(value - target) if value is not None else 0.6)
    if phase.tempo_bpm_range:
        lower, upper = phase.tempo_bpm_range
        tempo = track["tempo"]
        cost += 0.5 if tempo is None else max(lower - tempo, tempo - upper, 0) / 100
    if phase.vocals_preference != "any":
        target = 1 if phase.vocals_preference == "instrumental" else 0
        value = track["instrumentalness"]
        cost += 0.6 * (abs(value - target) if value is not None else 0.6)
    return cost


def diverse_shortlist(candidates, size):
    groups = defaultdict(list)
    for track in candidates:
        names = track.get("artist_names", [])
        groups[names[0] if names else track["track_id"]].append(track)
    result = []
    depth = 0
    while len(result) < size:
        added = False
        for group in groups.values():
            if depth < len(group):
                result.append(group[depth])
                added = True
                if len(result) == size:
                    break
        if not added:
            break
        depth += 1
    return result


def track_allowed(track, spec, phase=None):
    genres = set(track.get("genres", []))
    artists = set(track.get("artist_names", []))
    if track["track_id"] in spec.excluded_track_ids:
        return False
    if spec.content.explicit == "exclude" and track["explicit"]:
        return False
    if genres & {normalize(value) for value in spec.genres.excluded}:
        return False
    if spec.genres.allowed is not None and not genres & {
        normalize(value) for value in spec.genres.allowed
    }:
        return False
    if artists & {normalize(value) for value in spec.artists.excluded}:
        return False
    if spec.artists.only is not None and not artists & {
        normalize(value) for value in spec.artists.only
    }:
        return False
    if phase is not None:
        for name, (lower, upper) in phase.hard_feature_ranges.items():
            value = track[name]
            if value is None or not lower <= value <= upper:
                return False
    return True


def _fits_artist_cap(track, counts, cap):
    return cap is None or all(counts[name] < cap for name in track["artist_names"])


def _count_artists(tracks):
    return Counter(name for track in tracks for name in track["artist_names"])


def build_playlist(spec, pools, scores, fixed=None, frozen_phases=()):
    schedule = phase_schedule(spec)
    groups = {
        phase.id: list((fixed or {}).get(phase.id, [])) for phase, _, _ in schedule
    }
    selected = {track["track_id"] for group in groups.values() for track in group}
    counts = _count_artists([track for group in groups.values() for track in group])
    cap = spec.diversity.max_tracks_per_artist
    penalty = {"low": 0.03, "balanced": 0.15, "high": 0.35}[spec.diversity.preference]
    target_count = spec.length.target_track_count
    allocations = []
    if target_count:
        total = schedule[-1][2]
        weights = [(end - start) / total for _, start, end in schedule]
        allocations = [int(target_count * weight) for weight in weights]
        for index in sorted(
            range(len(weights)),
            key=lambda i: target_count * weights[i] - allocations[i],
            reverse=True,
        )[: target_count - sum(allocations)]:
            allocations[index] += 1

    for index, (phase, start, end) in enumerate(schedule):
        if phase.id in frozen_phases:
            continue
        group = groups[phase.id]
        goal = end - start
        duration = sum(track["duration_ms"] for track in group)
        while True:
            if target_count and len(group) >= allocations[index]:
                break
            if not target_count and duration >= goal:
                break
            candidates = [
                track
                for track in pools[phase.id]
                if track["track_id"] not in selected
                and _fits_artist_cap(track, counts, cap)
            ]
            if not candidates:
                break

            def cost(track):
                relevance = scores.get(phase.id, {}).get(track["track_id"], 0.0)
                repetition = max(
                    (counts[name] for name in track["artist_names"]), default=0
                )
                fit = abs(goal - duration - track["duration_ms"]) / max(goal, 1)
                transition = 0
                if (
                    group
                    and group[-1]["energy"] is not None
                    and track["energy"] is not None
                ):
                    transition = abs(group[-1]["energy"] - track["energy"]) * 0.15
                return (
                    feature_cost(track, phase)
                    - 0.7 * relevance
                    + penalty * repetition
                    + 0.2 * fit
                    + transition,
                    track["track_id"],
                )

            track = min(candidates, key=cost)
            if (
                not target_count
                and group
                and abs(goal - duration) <= abs(goal - duration - track["duration_ms"])
            ):
                break
            group.append(track)
            selected.add(track["track_id"])
            duration += track["duration_ms"]
            counts.update(track["artist_names"])
    return [track["track_id"] for phase, _, _ in schedule for track in groups[phase.id]]


def validate_playlist(spec, ids, catalogue):
    tracks = catalogue.tracks(ids)
    found = {track["track_id"] for track in tracks}
    errors = []
    if not ids:
        errors.append("Playlist is empty.")
    unknown = set(ids) - found
    if unknown:
        errors.append(f"Unknown track IDs: {', '.join(sorted(unknown))}")
    duplicate_count = len(ids) - len(set(ids))
    if duplicate_count:
        errors.append(f"Repeated track IDs: {duplicate_count}")
    total = sum(track["duration_ms"] for track in tracks)
    target = spec.length.target_duration_s
    duration_error = total / 1000 - target if target is not None else None
    if (
        target is not None
        and not -spec.length.tolerance_before_s
        <= duration_error
        <= spec.length.tolerance_after_s
    ):
        errors.append(
            f"Duration error {duration_error:+.1f}s is outside "
            f"[-{spec.length.tolerance_before_s}, +{spec.length.tolerance_after_s}]s."
        )
    if (
        spec.length.target_track_count is not None
        and len(ids) != spec.length.target_track_count
    ):
        errors.append(
            f"Expected {spec.length.target_track_count} tracks, found {len(ids)}."
        )
    absent_locks = set(spec.locked_track_ids) - set(ids)
    if absent_locks:
        errors.append(f"Missing locked tracks: {len(absent_locks)}")
    counts = _count_artists(tracks)
    cap = spec.diversity.max_tracks_per_artist
    if cap is not None:
        over = {name: count for name, count in counts.items() if count > cap}
        if over:
            errors.append(f"Artist limit exceeded: {over}")
    schedule = phase_schedule(spec, total_ms=max(total, 1))
    profile = {
        phase.id: {
            "duration_ms": 0,
            "energy_sum": 0,
            "known_energy_ms": 0,
            "deviation_sum": 0,
        }
        for phase, _, _ in schedule
    }
    elapsed = 0
    for track in tracks:
        if not track_allowed(track, spec):
            errors.append(f"Track violates a hard filter: {track['title']}")
        end = elapsed + track["duration_ms"]
        for phase, phase_start, phase_end in schedule:
            overlap = max(0, min(end, phase_end) - max(elapsed, phase_start))
            if not overlap:
                continue
            if not track_allowed(track, spec, phase):
                errors.append(
                    f"Hard phase feature range violated: {phase.id} / {track['title']}"
                )
            entry = profile[phase.id]
            entry["duration_ms"] += overlap
            if track["energy"] is not None:
                entry["known_energy_ms"] += overlap
                entry["energy_sum"] += overlap * track["energy"]
                if phase.energy_target is not None:
                    entry["deviation_sum"] += overlap * abs(
                        track["energy"] - phase.energy_target
                    )
        elapsed = end
    phases = []
    for phase, _, _ in schedule:
        entry = profile[phase.id]
        known = entry["known_energy_ms"]
        phases.append(
            {
                "id": phase.id,
                "target_energy": phase.energy_target,
                "mean_energy": entry["energy_sum"] / known if known else None,
                "energy_deviation": entry["deviation_sum"] / known
                if known and phase.energy_target is not None
                else None,
            }
        )
    warnings = [
        f"Energy profile differs from target in phase {phase['id']}: "
        f"mean {phase['mean_energy']:.2f}, target {phase['target_energy']:.2f}."
        for phase in phases
        if phase["energy_deviation"] is not None
        and phase["energy_deviation"] > ENERGY_DEVIATION_WARNING
    ]
    return {
        "passed": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": warnings,
        "track_count": len(ids),
        "duration_s": total / 1000,
        "duration_error_s": duration_error,
        "valid_id_rate": sum(track_id in found for track_id in ids) / len(ids)
        if ids
        else 0,
        "duplicate_count": duplicate_count,
        "phases": phases,
    }


def _sequence_cost(spec, tracks):
    total = sum(track["duration_ms"] for track in tracks)
    schedule = phase_schedule(spec, total_ms=max(total, 1))
    energy_error, known_duration, hard_violations = 0, 0, 0
    phase_errors = {phase.id: [0, 0] for phase, _, _ in schedule}
    elapsed = 0
    for track in tracks:
        end = elapsed + track["duration_ms"]
        for phase, start, phase_end in schedule:
            overlap = max(0, min(end, phase_end) - max(elapsed, start))
            if not overlap:
                continue
            if phase.hard_feature_ranges and not track_allowed(track, spec, phase):
                hard_violations += 1
            if phase.energy_target is not None and track["energy"] is not None:
                energy_error += overlap * abs(track["energy"] - phase.energy_target)
                known_duration += overlap
                phase_errors[phase.id][0] += overlap * abs(
                    track["energy"] - phase.energy_target
                )
                phase_errors[phase.id][1] += overlap
        elapsed = end
    target = spec.length.target_duration_s
    offset = total - target * 1000 if target is not None else 0
    error = abs(offset)
    excess = max(
        0,
        -offset - spec.length.tolerance_before_s * 1000,
        offset - spec.length.tolerance_after_s * 1000,
    )
    return (
        hard_violations,
        excess,
        max(
            (error / duration for error, duration in phase_errors.values() if duration),
            default=0,
        ),
        energy_error / known_duration if known_duration else 0,
        error,
    )


def polish_playlist(spec, ids, pools, catalogue, protected_ids=(), frozen_phases=()):
    tracks = catalogue.tracks(ids)
    target = spec.length.target_duration_s
    if target is None and not any(
        phase.energy_target is not None for phase in spec.phases
    ):
        return ids
    protected = set(spec.locked_track_ids) | set(protected_ids)
    phase_by_id = {phase.id: phase for phase, _, _ in phase_schedule(spec)}
    for _ in range(8):
        duration = sum(track["duration_ms"] for track in tracks)
        best, best_cost = None, _sequence_cost(spec, tracks)
        if best_cost[:2] == (0, 0) and best_cost[2] <= ENERGY_DEVIATION_WARNING:
            break
        used = {track["track_id"] for track in tracks}
        counts = _count_artists(tracks)
        schedule = phase_schedule(spec, total_ms=max(duration, 1))
        elapsed = 0
        for index, old in enumerate(tracks):
            midpoint = elapsed + old["duration_ms"] / 2
            elapsed += old["duration_ms"]
            if old["track_id"] in protected:
                continue
            if spec.length.target_track_count is None and len(tracks) > 1:
                cost = _sequence_cost(spec, tracks[:index] + tracks[index + 1 :])
                if cost < best_cost:
                    best, best_cost = ("remove", index, None), cost
            phase_id = next(
                (phase.id for phase, start, end in schedule if start <= midpoint < end),
                schedule[-1][0].id,
            )
            candidates = pools.get(phase_id, [])
            remaining = counts.copy()
            remaining.subtract(old["artist_names"])
            for new in candidates:
                if new["track_id"] in used or not _fits_artist_cap(
                    new, remaining, spec.diversity.max_tracks_per_artist
                ):
                    continue
                if track_allowed(new, spec, phase_by_id[phase_id]):
                    cost = _sequence_cost(
                        spec, [*tracks[:index], new, *tracks[index + 1 :]]
                    )
                    if cost < best_cost:
                        best, best_cost = ("swap", index, new), cost
        if spec.length.target_track_count is None:
            for phase_id, pool in pools.items():
                if phase_id in frozen_phases:
                    continue
                phase_end = next(
                    end for phase, _, end in schedule if phase.id == phase_id
                )
                elapsed, position = 0, 0
                for track in tracks:
                    if elapsed + track["duration_ms"] / 2 >= phase_end:
                        break
                    elapsed += track["duration_ms"]
                    position += 1
                for new in pool:
                    if (
                        new["track_id"] not in used
                        and _fits_artist_cap(
                            new, counts, spec.diversity.max_tracks_per_artist
                        )
                        and track_allowed(new, spec, phase_by_id[phase_id])
                    ):
                        cost = _sequence_cost(
                            spec, [*tracks[:position], new, *tracks[position:]]
                        )
                        if cost < best_cost:
                            best, best_cost = ("add", position, new), cost
        if best is None:
            break
        action, index, new = best
        if action == "remove":
            tracks.pop(index)
        elif action == "swap":
            tracks[index] = new
        else:
            tracks.insert(index, new)
    return [track["track_id"] for track in tracks]
