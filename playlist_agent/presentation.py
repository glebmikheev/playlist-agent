import csv
import html
import json
from pathlib import Path

from .models import phase_schedule


def describe_spec(spec):
    length = spec.length
    lines = []
    if length.target_duration_s is not None:
        lines.append(
            f"{length.target_duration_s / 60:g} мин ± {length.tolerance_s / 60:g} мин"
        )
    if length.target_track_count is not None:
        lines.append(f"{length.target_track_count} треков")
    text = "**Понял запрос:** " + "; ".join(lines) + "."
    if spec.semantic_brief:
        text += "\n\n" + html.escape(spec.semantic_brief)
    limits = []
    for label, values in [
        ("Желательно", spec.genres.preferred),
        ("Только жанры", spec.genres.allowed),
        ("Без жанров", spec.genres.excluded),
        ("В духе", spec.artists.reference),
        ("Только исполнители", spec.artists.only),
        ("Без исполнителей", spec.artists.excluded),
    ]:
        if values:
            limits.append(label + ": " + html.escape(", ".join(values)))
    if spec.diversity.max_tracks_per_artist is not None:
        limits.append(
            f"Не больше {spec.diversity.max_tracks_per_artist} треков одного исполнителя"
        )
    if spec.content.explicit == "exclude":
        limits.append("Без explicit")
    if spec.locked_track_ids:
        limits.append(f"Закреплено: {len(spec.locked_track_ids)}")
    if limits:
        text += "\n\n" + "; ".join(limits) + "."
    phases = []
    for phase, start, end in phase_schedule(spec):
        label = html.escape(phase.description or phase.id)
        if length.target_duration_s is not None:
            label += f" ({(end - start) / 60000:g} мин)"
        if phase.energy_target is not None:
            label += f", энергия {phase.energy_target:g}"
        phases.append(label)
    if len(phases) > 1 or any(phase.energy_target is not None for phase in spec.phases):
        text += "\n\n**Профиль:** " + " → ".join(phases) + "."
    inferred = [
        html.escape(item.field + " = " + item.value)
        for item in spec.assumptions
        if item.origin == "inferred"
    ]
    if inferred:
        text += "\n\n**Допущения:** " + "; ".join(inferred) + "."
    return text


def profile_svg(spec, tracks):
    if not tracks:
        return ""
    total = sum(track["duration_ms"] for track in tracks)
    left, top, width, height = 55, 20, 720, 160

    def x(value):
        return left + width * value / total

    def y(value):
        return top + height * (1 - value)

    parts = [
        '<svg viewBox="0 0 800 235" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Профиль энергии по времени">'
    ]
    for value in [0, 0.25, 0.5, 0.75, 1]:
        parts.append(
            f'<line x1="{left}" y1="{y(value)}" x2="775" y2="{y(value)}" stroke="#ddd"/><text x="20" y="{y(value) + 4}" font-size="11">{value:g}</text>'
        )
    for index in range(6):
        position = total * index / 5
        parts.append(
            f'<text x="{x(position)}" y="202" text-anchor="middle" font-size="11">{position / 60000:.1f}</text>'
        )
    elapsed = 0
    for track in tracks:
        end = elapsed + track["duration_ms"]
        if track["energy"] is not None:
            title = html.escape(track["title"] + " · " + track["artists"])
            parts.append(
                f'<line x1="{x(elapsed)}" y1="{y(track["energy"])}" x2="{x(end)}" y2="{y(track["energy"])}" stroke="#2563eb" stroke-width="3"><title>{title}</title></line>'
            )
        elapsed = end
    for phase, start, end in phase_schedule(spec, total):
        if phase.energy_target is not None:
            parts.append(
                f'<line x1="{x(start)}" y1="{y(phase.energy_target)}" x2="{x(end)}" y2="{y(phase.energy_target)}" stroke="#dc2626" stroke-width="2" stroke-dasharray="7 4"/>'
            )
    parts.append(
        '<text x="55" y="225" font-size="12" fill="#2563eb">Синий: энергия треков</text><text x="280" y="225" font-size="12" fill="#dc2626">Красный пунктир: цель</text><text x="700" y="225" font-size="12">Минуты</text></svg>'
    )
    return "".join(parts)


def table_rows(tracks):
    return [
        [
            index,
            track["title"],
            track["artists"],
            round(track["duration_ms"] / 60000, 2),
            track["energy"],
            ", ".join(track["genres"]),
            f"https://open.spotify.com/track/{track['track_id']}",
        ]
        for index, track in enumerate(tracks, 1)
    ]


def export_playlist(directory, session_id, spec, tracks):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, csv_path = (
        directory / f"{session_id}.json",
        directory / f"{session_id}.csv",
    )
    json_path.write_text(
        json.dumps(
            {"spec": spec.model_dump(mode="json"), "tracks": tracks},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "position",
                "title",
                "artists",
                "duration_min",
                "energy",
                "genres",
                "spotify_url",
            ]
        )
        for row in table_rows(tracks):
            writer.writerow(
                [
                    "'" + value
                    if isinstance(value, str)
                    and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                    for value in row
                ]
            )
    return [str(csv_path), str(json_path)]
