import re

from .catalogue import normalize
from .models import phase_schedule


def work_matches(title, work):
    """Match a work name against a track title, ignoring punctuation and numbering."""

    def words(text):
        text = re.sub(r"\b(?:no|number)\.?\s*(?=\d)", "", normalize(text))
        return " ".join(re.findall(r"[^\W_]+", text))

    needle = words(work)
    return bool(needle and re.search(r"\b" + re.escape(needle) + r"\b", words(title)))


def matches(track, selector):
    values = {normalize(value) for value in selector.values}
    if selector.kind == "artist":
        return bool(values & set(track["artist_names"]))
    if selector.kind == "genre":
        return bool(values & set(track["genres"]))
    return any(work_matches(track["title"], value) for value in selector.values)


def scoped_tracks(spec, tracks, phase_id):
    if phase_id is None:
        return tracks
    schedule = phase_schedule(
        spec, max(1, sum(track["duration_ms"] for track in tracks))
    )
    start, end = next(
        (start, end) for phase, start, end in schedule if phase.id == phase_id
    )
    result, elapsed = [], 0
    for track in tracks:
        midpoint = elapsed + track["duration_ms"] / 2
        if start <= midpoint < end:
            result.append(track)
        elapsed += track["duration_ms"]
    return result


def requirement_errors(spec, tracks):
    errors = []
    for rule in spec.requirements:
        count = sum(
            matches(track, rule.selector)
            for track in scoped_tracks(spec, tracks, rule.phase_id)
        )
        upper = rule.max_count if rule.max_count is not None else "any"
        if count < rule.min_count or (
            rule.max_count is not None and count > rule.max_count
        ):
            errors.append(
                f"Required {rule.selector.kind} {', '.join(rule.selector.values)}: "
                f"found {count}, expected {rule.min_count}..{upper}."
            )
    if spec.prefix and (
        len(tracks) < spec.prefix.count
        or not all(
            matches(track, spec.prefix.selector)
            for track in tracks[: spec.prefix.count]
        )
    ):
        errors.append(
            f"First {spec.prefix.count} tracks must match "
            f"{', '.join(spec.prefix.selector.values)}."
        )
    return errors


def within_maxima(spec, groups, candidate, phase_id):
    for rule in spec.requirements:
        if (
            rule.max_count is None
            or (rule.phase_id is not None and rule.phase_id != phase_id)
            or not matches(candidate, rule.selector)
        ):
            continue
        if rule.phase_id:
            tracks = groups.get(rule.phase_id, [])
        else:
            tracks = [track for group in groups.values() for track in group]
        if sum(matches(track, rule.selector) for track in tracks) >= rule.max_count:
            return False
    return True
