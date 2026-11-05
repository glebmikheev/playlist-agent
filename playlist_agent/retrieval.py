from itertools import zip_longest

from .models import Selector
from .selection import diverse_shortlist, feature_cost, track_allowed


def retrieve_candidates(catalogue, spec, phase, size):
    selectors = [
        Selector(kind="genre", values=[genre]) for genre in spec.genres.preferred
    ]
    selectors += [
        Selector(kind="artist", values=[artist]) for artist in spec.artists.reference
    ]
    selectors += [
        rule.selector
        for rule in spec.requirements
        if rule.phase_id in (None, phase.id) and rule.min_count
    ]
    if spec.prefix and phase.id == spec.phases[0].id:
        selectors.append(spec.prefix.selector)

    # A selector may be reached through several paths (a preferred genre that is
    # also required, for example), so collapse duplicates before running queries.
    unique = {(item.kind, tuple(item.values)): item for item in selectors}
    selectors = list(unique.values())

    groups = []
    for selector in selectors or [None]:
        limit = min(8000, max(2500, size * 25))
        if selector is None:
            candidates = catalogue.search(spec, phase, limit)
        else:
            candidates = catalogue.search(spec, phase, limit, selector=selector)
        candidates = [
            track for track in candidates if track_allowed(track, spec, phase)
        ]
        candidates.sort(key=lambda track: feature_cost(track, phase))
        groups.append(diverse_shortlist(candidates, size))

    # Interleave the per-selector shortlists so that no single genre or artist
    # dominates the front of the pool.
    ordered = {}
    for row in zip_longest(*groups):
        for track in row:
            if track is not None:
                ordered.setdefault(track["track_id"], track)
    return list(ordered.values())[:size]
