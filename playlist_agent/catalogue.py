import csv
import hashlib
import math
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

SOURCE_URL = "https://huggingface.co/datasets/maharshipandya/spotify-tracks-dataset/resolve/635b034f69257814eff850a5c2b3346fe458134f/dataset.csv"
SOURCE_SHA256 = "b202fa49909b2d5cef71a04b1d21243cfeb36414535f2ca9272aa646721177bd"
FEATURES = (
    "energy",
    "valence",
    "tempo",
    "danceability",
    "instrumentalness",
    "acousticness",
)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def prepare_catalogue(source: Path, destination: Path):
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != SOURCE_SHA256:
        raise ValueError("The source CSV does not match the pinned dataset revision.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".building.sqlite")
    if temporary.exists():
        temporary.unlink()

    rows = []
    genre_rows = set()
    artist_rows = set()
    seen = set()
    rejected = 0
    with source.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            track_id = row["track_id"]
            title = row["track_name"].strip()
            artists = row["artists"].strip()
            duration = int(row["duration_ms"])
            energy = float(row["energy"])
            if not title or not artists or duration <= 0 or not 0 <= energy <= 1:
                rejected += 1
                continue

            genre_rows.add((track_id, normalize(row["track_genre"])))
            for artist in artists.split(";"):
                if artist.strip():
                    artist_rows.add((track_id, normalize(artist)))

            if track_id in seen:
                continue
            seen.add(track_id)

            values = []
            for feature in FEATURES:
                value = float(row[feature])
                if not math.isfinite(value) or (feature == "tempo" and value <= 0):
                    value = None
                elif feature != "tempo" and not 0 <= value <= 1:
                    value = None
                values.append(value)
            rows.append(
                (
                    track_id,
                    title,
                    artists,
                    duration,
                    row["explicit"].lower() == "true",
                    *values,
                )
            )

    with closing(sqlite3.connect(temporary)) as connection:
        connection.executescript("""
            CREATE TABLE tracks (
                track_id TEXT PRIMARY KEY, title TEXT NOT NULL, artists TEXT NOT NULL,
                duration_ms INTEGER NOT NULL, explicit INTEGER NOT NULL,
                energy REAL, valence REAL, tempo REAL, danceability REAL,
                instrumentalness REAL, acousticness REAL
            );
            CREATE TABLE track_genres (track_id TEXT, genre TEXT, PRIMARY KEY(track_id, genre));
            CREATE TABLE track_artists (track_id TEXT, artist TEXT, PRIMARY KEY(track_id, artist));
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        """)
        connection.executemany(
            "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        connection.executemany(
            "INSERT INTO track_genres VALUES (?,?)", sorted(genre_rows)
        )
        connection.executemany(
            "INSERT INTO track_artists VALUES (?,?)", sorted(artist_rows)
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("source_url", SOURCE_URL),
                ("source_sha256", checksum),
                ("track_count", str(len(rows))),
                ("rejected_rows", str(rejected)),
            ],
        )
        connection.executescript("""
            CREATE INDEX genres_lookup ON track_genres(genre, track_id);
            CREATE INDEX artists_lookup ON track_artists(artist, track_id);
            CREATE INDEX energy_lookup ON tracks(energy);
        """)
        connection.commit()
    temporary.replace(destination)
    return {
        "tracks": len(rows),
        "genre_links": len(genre_rows),
        "rejected_rows": rejected,
        "database_bytes": destination.stat().st_size,
    }


class Catalogue:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.exists():
            raise FileNotFoundError(
                "Catalogue missing. Run python prepare_data.py first."
            )

    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def vocabulary(self):
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT DISTINCT genre FROM track_genres ORDER BY genre"
            )
            return [row[0] for row in rows]

    def tracks(self, ids):
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with closing(self.connect()) as connection:
            records = {
                row["track_id"]: dict(row)
                for row in connection.execute(
                    f"SELECT * FROM tracks WHERE track_id IN ({placeholders})", ids
                )
            }
            for row in connection.execute(
                f"SELECT * FROM track_genres WHERE track_id IN ({placeholders})", ids
            ):
                records[row["track_id"]].setdefault("genres", []).append(row["genre"])
            for row in connection.execute(
                f"SELECT * FROM track_artists WHERE track_id IN ({placeholders})", ids
            ):
                records[row["track_id"]].setdefault("artist_names", []).append(
                    row["artist"]
                )
        return [records[track_id] for track_id in ids if track_id in records]

    def resolve(self, references, current_ids):
        resolved = []
        with closing(self.connect()) as connection:
            for reference in references:
                text = str(reference).strip()
                if text.isdigit() and 1 <= int(text) <= len(current_ids):
                    track_id = current_ids[int(text) - 1]
                else:
                    text = (
                        text.split("?")[0]
                        .rstrip("/")
                        .rsplit("/", 1)[-1]
                        .removeprefix("spotify:track:")
                    )
                    rows = connection.execute(
                        "SELECT track_id FROM tracks "
                        "WHERE track_id = ? OR lower(title) = lower(?) LIMIT 2",
                        (text, text),
                    ).fetchall()
                    if len(rows) != 1:
                        raise ValueError(
                            f"Track reference is missing or ambiguous: {reference}"
                        )
                    track_id = rows[0][0]
                if track_id not in resolved:
                    resolved.append(track_id)
        return resolved

    def search(self, spec, phase, limit=1000, selector=None):
        clauses = []
        parameters = []

        restrictions = [
            ("track_genres", "genre", spec.genres.allowed, False),
            ("track_genres", "genre", spec.genres.excluded, True),
            ("track_artists", "artist", spec.artists.only, False),
            ("track_artists", "artist", spec.artists.excluded, True),
        ]
        for table, field, values, excluded in restrictions:
            if values is None or (not values and excluded):
                continue
            if not values:
                clauses.append("0")
                continue
            marks = ",".join("?" for _ in values)
            exists = (
                f"EXISTS (SELECT 1 FROM {table} f "
                f"WHERE f.track_id=t.track_id AND f.{field} IN ({marks}))"
            )
            clauses.append(("NOT " if excluded else "") + exists)
            parameters.extend(normalize(value) for value in values)

        if spec.content.explicit == "exclude":
            clauses.append("t.explicit=0")
        if spec.excluded_track_ids:
            marks = ",".join("?" for _ in spec.excluded_track_ids)
            clauses.append(f"t.track_id NOT IN ({marks})")
            parameters.extend(spec.excluded_track_ids)
        if phase.genres:
            marks = ",".join("?" for _ in phase.genres)
            clauses.append(
                f"EXISTS (SELECT 1 FROM track_genres p "
                f"WHERE p.track_id=t.track_id AND p.genre IN ({marks}))"
            )
            parameters.extend(normalize(value) for value in phase.genres)

        if selector is not None:
            if selector.kind in {"artist", "genre"}:
                table, field = {
                    "artist": ("track_artists", "artist"),
                    "genre": ("track_genres", "genre"),
                }[selector.kind]
                marks = ",".join("?" for _ in selector.values)
                clauses.append(
                    f"EXISTS (SELECT 1 FROM {table} s "
                    f"WHERE s.track_id=t.track_id AND s.{field} IN ({marks}))"
                )
                parameters.extend(normalize(value) for value in selector.values)
            else:
                title_matches = " OR ".join(
                    "instr(lower(t.title), lower(?)) > 0" for _ in selector.values
                )
                clauses.append("(" + title_matches + ")")
                parameters.extend(selector.values)

        for field, bounds in phase.hard_feature_ranges.items():
            if field not in FEATURES:
                raise ValueError("Unsupported audio feature.")
            clauses.append(f"t.{field} BETWEEN ? AND ?")
            parameters.extend(bounds)

        where = " AND ".join(clauses) if clauses else "1"
        score = "0.0"
        score_parameters = []
        if phase.energy_target is not None:
            score += " + coalesce(abs(t.energy-?), 1)"
            score_parameters.append(phase.energy_target)
        if phase.tempo_bpm_range is not None:
            score += " + coalesce(max(?-t.tempo, t.tempo-?, 0)/100.0, 1)"
            score_parameters.extend(phase.tempo_bpm_range)
        if spec.genres.preferred:
            marks = ",".join("?" for _ in spec.genres.preferred)
            score += (
                " - 0.35 * EXISTS (SELECT 1 FROM track_genres g "
                f"WHERE g.track_id=t.track_id AND g.genre IN ({marks}))"
            )
            score_parameters.extend(normalize(value) for value in spec.genres.preferred)

        with closing(self.connect()) as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    f"SELECT t.track_id FROM tracks t WHERE {where} "
                    f"ORDER BY {score}, t.track_id LIMIT ?",
                    [*parameters, *score_parameters, limit],
                )
            ]
        return self.tracks(ids)
