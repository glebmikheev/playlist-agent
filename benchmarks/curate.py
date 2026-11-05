"""Catalogue-backed reference examples, independent of the agent's retrieval."""

from itertools import combinations

# Pools were selected by musical intent, not from the agent's answers.
METAL = """
6UB9mShVLbMm0W4e6vud4C 5sICkBXVmaCQk5aISGR3x1 2MuWTIM3b0YEAskbeeFE1i
5IX4TbIR5mMHGE4wiWwKW0 2O7UGwJPVlix15Wn4sa5vw 28WmNsclKsrVmdv3tDmoYU
6FUwPb4mGlUDbx42uspXaZ 2dXsILW8gzkosqleHAvl0v 0L7zm6afBEtrNKo6C6Gj08
4f3RDq9nYPBeR1yMSgnmBm 0IXpUl1fn2QZcBavfuq0H4 2RaA6kIcvomt77qlIgGhCT
6p8eFfPw3nQkf37aT3AkmK 469rBLYJUZHMJLtq2Wch3h 2SrVGpjv0mqCR6j9EZPSm2
6TYQRlRci6AkVrN9a5C7ne 4OMgqSsMbVhPCFnivcNAqh 755bvJDSA3LNcNuFgBT9LK
0ElAcOj8tudMA6X6KZ7SMO 3CIOopLwvyMvXk97ZEksKO 2dbOYFfS7r7NAzquRvji9A
3crIa21lfjekujCQuGjlJT 7iOSeHfKU2EhSKcX7TbjKG 3V417nSM4Ilh0Tt5CqustV
5PkfL56EWMQlFrIakXjrsV 4q9YY8eorYGjpjk3Zko7l9 6ckozbGmOusAnLT2LEbERy
""".split()

POP = """
1u8c2t2Cy7UBoG4ArRcF5g 1D4PL9B8gOg78jiHg3FvBb 6vJaXvEXh6HOeiEZLFDhrp
6ic8OlLUNEATToEFU3xmaH 20I6sIOMTCkB6w7ryavxtO 1kPpge9JDLpcj15qgrPbYX
2hHLbkatPwOOmrNxTiD41L 7oBxxv6C5ttINeFHzOVwS4 69C7u1nZcyZmuZXNNF5U9C
3VBXcradZOCVLaAh7FIcSi 76FZM38RC8XaAjJ77CVTNe 7xoUc6faLbCqZO6fQEYprd
2qT1uLXPVPzGgFOx4jtEuo 3e9HZxeyfWwjeyPAMmWSSQ 6ocbgoVGwYJhOv1GgI9NsF
0BVslzxSY5Z78WboakxrB1 017PF4Q3l4DBUiWoXk4OWT 3PfIrDoz19wz7qK7tYeu62
0VjIjW4GlUZAMYd2vXMi3b 4LRPiXqCikLlN15c3yImP7 4h9wh7iOZ0GGn8QVp4RAOB
4uUG5RXrOk84mYEfFvj3cK
""".split()

MOZART = """
0VHwSfD7ItMR97BzjbWpPG 2nfL62mzeFMzhGZ1YHWGeU 429GaRIWKJ3EU1GAQPbLcr
64ta7ZvTJeThz7Ns4LmAHS 3oHSL6pt9LpNrQZuQGu9wL
""".split()

PIANO = """
5NGtFXVpXSvwunEIGeviY3 3DNRdudZ2SstnDCVKFdXxG 5u5aVJKjSMJr4zesMPz7bL
1VNvsvEsUpuUCbHpVop1vo 2MSgFefjK0T7Iwjvr3OKqV 3weNRklVDqb4Rr5MhKBR3D
0Dkibk70FDp6t7eOZNemNQ 2agBDIr9MYDUducQPC1sFU
""".split()

AMBIENT = """
3bCmDqflFBHijgJfvtqev5 1vgSaC0BPlL6LEm4Xsx59J 7Jd0MqZq15T4pDWts2G6Yl
2d9KBVxb6bUfVCjjv1JAVe 1WqVCOMjOOWjamob3A2DsK 3rLe832ZEZfpBmkgXGSrtX
2UZZtkoLOg7IHxeTAdPFvd 70FfytITXJOz0s2xY3rOzz 56rN3Zh03LVnJ5kR1VTRJN
2fTIn1AqeTGR6tSSfiS8zk 7b2dMRCVDx7OGekIVjCrs1 1Mua0g96hUldJWj00GDsel
4TrCvd1PrqI14lZb7tEtYo 11n8LudggXuZXmldSlAadR 6QSOIejoVt6sFkPHL4JGb5
4MzII8fszi8KkFl1ryv07L 3JpzqRcjxif901JZKAPLn6 7MZLPKriK0CMy6a0ovE1Ca
1j7fq3bwLM63RMFvceb2Fm 7B87dSMUgWM2Taj0EEXtjp 0giZdhbuEYl7uGJaeVuYCz
0tVfMLnBs02zD6RhUmXnVs 0PUIbaTAf0iMmoHCdNiQve 2MZSXhq4XDJWu6coGoXX1V
2Bc4llhjJBW77I552RgA3L
""".split()

CLASSICAL = (
    MOZART
    + """
0x98TljIUZtLffQkRPoVb6 17i5jLpzndlQhbS4SrTd0B 0jOnZhF75V68VsBObWx2XO
""".split()
    + PIANO[:5]
)


def fit_duration(pool, target_s, tracks, required=()):
    """Fit whole tracks from a curated pool; preserve its editorial order."""
    required = set(required)
    pool = list(dict.fromkeys(pool))
    remaining = target_s - sum(tracks[key]["duration_ms"] / 1000 for key in required)

    # Reachable totals map to the track IDs that produce them, so the result
    # keeps the order of the original pool rather than the selection order.
    paths = {0: ()}
    for key in pool:
        if key in required:
            continue
        seconds = round(tracks[key]["duration_ms"] / 1000)
        for duration, ids in list(paths.items()):
            total = duration + seconds
            if total <= remaining + 300 and total not in paths:
                paths[total] = (*ids, key)

    best = min(paths, key=lambda seconds: abs(seconds - remaining))
    selected = set(paths[best]) | required
    result = [key for key in pool if key in selected]
    actual = sum(tracks[key]["duration_ms"] / 1000 for key in result)
    if not target_s - 180 <= actual <= target_s + 300:
        raise ValueError(f"Curated pool cannot fit {target_s}s: {actual}s")
    return result


def create_references(catalogue):
    rock = "003vvx7Niy0yvhvHt4a68B 5T8EDUDqKcs6OSOwEsfqG7 0BxE4FqsDD1Ot4YuBXwAPp 5FVd6KXrgO9B3JPmC8OPst 1JSTJqkT5qHq8MDJnJbRE1".split()
    country = (
        "1QbOvACeYanja5pbnJbAmk 3fqwjXwUGN6vbzIwvyFMhx 2rxQMGVafnNaRaXlRMWPde".split()
    )
    hiphop = "0eEXcw3JLVXcRxYrVYMy68"
    rap = "4sGylyGhQaOzhi77fLrMmP 6Pzm6HXxN0cwfCWM7kngXL 2Ja3UauMDeQnq4sI8a64Ys 3ia3dJETSOllPsv3LJkE35 503OTo2dSqe7qk76rgsbep 4LwU4Vp6od3Sb08CsP99GC 0oNzFzjf5eDRnvpGe7u2r2 5g9lS8deSIxItFBmZRC4vN 2CPturRUlpvirYr7VpkXCV 7iXF2W9vKmDoGAhlHdpyIa".split()

    ids = list(
        dict.fromkeys(
            METAL + POP + CLASSICAL + AMBIENT + PIANO + rock + country + [hiphop] + rap
        )
    )
    tracks = {track["track_id"]: track for track in catalogue.tracks(ids)}
    missing = set(ids) - tracks.keys()
    if missing:
        raise ValueError(f"Reference IDs missing from catalogue: {sorted(missing)}")

    def fit(pool, target, required=()):
        return fit_duration(pool, target, tracks, required)

    references = {}
    references[1] = fit(METAL, 7200, METAL[:2])
    references[2] = fit(METAL[8:] + POP[:1], 3600, [METAL[8], POP[0]])
    mixed = [key for pair in zip(METAL[8:], POP) for key in pair]
    references[3] = fit(mixed, 3600, mixed[:10])
    references[4] = POP[:11] + [METAL[1]] + POP[11:] + [METAL[2]]
    references[5] = []
    references[6] = fit(
        [POP[18], rock[1], rock[0], POP[19], METAL[19], METAL[11], METAL[4]],
        1500,
        [POP[18], rock[1], rock[0]],
    )

    mixed_ambient = [
        AMBIENT[1],
        AMBIENT[2],
        AMBIENT[7],
        AMBIENT[12],
        *rock,
        *country,
        CLASSICAL[6],
    ]
    references[7] = fit(
        mixed_ambient, 3000, [AMBIENT[1], rock[0], *country, CLASSICAL[6]]
    )
    references[8] = fit(
        CLASSICAL, 3600, [CLASSICAL[5], CLASSICAL[6], PIANO[0], PIANO[2]]
    )
    references[9] = MOZART
    references[10] = MOZART[:3] + [MOZART[4], MOZART[3]]
    references[11] = MOZART
    references[12] = fit(AMBIENT + PIANO, 10800, [AMBIENT[0], AMBIENT[6]])

    sauna = fit(
        PIANO[:3]
        + [AMBIENT[1], AMBIENT[2], AMBIENT[7], AMBIENT[8], AMBIENT[9], hiphop],
        2400,
        [AMBIENT[1], hiphop],
    )
    sauna.remove(hiphop)
    half = sum(tracks[key]["duration_ms"] for key in sauna) / 2
    subsets = (
        part for size in range(len(sauna) + 1) for part in combinations(sauna, size)
    )
    before = min(
        subsets,
        key=lambda part: abs(sum(tracks[key]["duration_ms"] for key in part) - half),
    )
    references[13] = (
        list(before) + [hiphop] + [key for key in sauna if key not in before]
    )

    prefix = [POP[0], POP[18], POP[19]]
    references[14] = fit(prefix + rap, 2700, prefix + rap[:1] + rap[3:4])

    opening = [PIANO[2], PIANO[0], PIANO[1], PIANO[4]]
    tail = [
        AMBIENT[14],
        AMBIENT[13],
        AMBIENT[1],
        AMBIENT[7],
        AMBIENT[8],
        AMBIENT[11],
        AMBIENT[12],
        AMBIENT[4],
        AMBIENT[9],
        AMBIENT[0],
    ]
    references[15] = fit(
        opening + tail, 5400, opening + [AMBIENT[1], AMBIENT[9], AMBIENT[0]]
    )
    return references, tracks
