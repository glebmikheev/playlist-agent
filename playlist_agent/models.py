from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0, le=1)]
FeatureName = Literal[
    "energy", "valence", "tempo", "instrumentalness", "acousticness", "danceability"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Length(Model):
    target_duration_s: int | None = Field(default=None, ge=60, le=43200)
    tolerance_before_s: int = Field(default=180, ge=0, le=1800)
    tolerance_after_s: int = Field(default=300, ge=0, le=1800)
    target_track_count: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_tolerance(cls, value):
        if isinstance(value, dict) and "tolerance_s" in value:
            value = dict(value)
            tolerance = value.pop("tolerance_s")
            value.setdefault("tolerance_before_s", tolerance)
            value.setdefault("tolerance_after_s", tolerance)
        return value


class Genres(Model):
    preferred: list[str] = Field(default_factory=list)
    allowed: list[str] | None = None
    excluded: list[str] = Field(default_factory=list)


class Artists(Model):
    reference: list[str] = Field(default_factory=list)
    only: list[str] | None = None
    excluded: list[str] = Field(default_factory=list)


class Phase(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    description: str = Field(default="", max_length=500)
    duration_s: int | None = Field(default=None, ge=1, le=43200)
    energy_target: Unit | None = None
    valence_target: Unit | None = None
    tempo_bpm_range: tuple[float, float] | None = None
    vocals_preference: Literal["any", "instrumental", "vocals"] = "any"
    hard_feature_ranges: dict[FeatureName, tuple[float, float]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def check_ranges(self):
        ranges = dict(self.hard_feature_ranges)
        if self.tempo_bpm_range is not None:
            ranges["tempo"] = self.tempo_bpm_range
        for name, (lower, upper) in ranges.items():
            if lower > upper or lower < 0 or (name != "tempo" and upper > 1):
                raise ValueError(f"Invalid {name} range.")
        return self


class Diversity(Model):
    preference: Literal["low", "balanced", "high"] = "balanced"
    max_tracks_per_artist: int | None = Field(default=None, ge=1, le=100)


class Content(Model):
    explicit: Literal["allow", "exclude"] = "allow"


class TrackRefs(Model):
    keep_refs: list[str] = Field(default_factory=list)
    exclude_refs: list[str] = Field(default_factory=list)


class Assumption(Model):
    field: str
    origin: Literal["default", "inferred"]
    value: str


class PlaylistSpec(Model):
    length: Length = Field(default_factory=Length)
    semantic_brief: str = Field(default="", max_length=2000)
    genres: Genres = Field(default_factory=Genres)
    artists: Artists = Field(default_factory=Artists)
    phases: list[Phase] = Field(default_factory=list, max_length=3)
    diversity: Diversity = Field(default_factory=Diversity)
    content: Content = Field(default_factory=Content)
    locked_track_ids: list[str] = Field(default_factory=list)
    excluded_track_ids: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_phases(self):
        ids = [phase.id for phase in self.phases]
        if len(ids) != len(set(ids)):
            raise ValueError("Phase IDs must be unique.")
        target = self.length.target_duration_s
        given = sum(phase.duration_s or 0 for phase in self.phases)
        if target is not None and self.phases:
            if given > target:
                raise ValueError("Phase durations exceed the requested total.")
            if (
                all(phase.duration_s is not None for phase in self.phases)
                and given != target
            ):
                raise ValueError("Phase durations must sum to the requested duration.")
        if set(self.locked_track_ids) & set(self.excluded_track_ids):
            raise ValueError("A track cannot be both locked and excluded.")
        return self


class SpecPatch(Model):
    length: Length | None = None
    semantic_brief: str | None = None
    genres: Genres | None = None
    artists: Artists | None = None
    phases: list[Phase] | None = Field(default=None, max_length=3)
    diversity: Diversity | None = None
    content: Content | None = None


class Moderation(Model):
    decision: Literal["allow", "block"]
    category: Literal[
        "none",
        "harassment",
        "hate_speech",
        "sexually_explicit",
        "dangerous_content",
        "child_safety",
        "self_harm",
        "privacy_abuse",
        "jailbreak",
        "other",
    ]
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def check_decision(self):
        if (self.decision == "allow") != (self.category == "none"):
            raise ValueError(
                "Allowed content must use category none; blocked content needs a category."
            )
        return self


class Interpretation(Model):
    moderation: Moderation
    action: Literal["create", "edit", "clarify", "reject"]
    edit_scope: str = "all"
    replace_phases: bool = False
    update: SpecPatch = Field(default_factory=SpecPatch)
    tracks: TrackRefs = Field(default_factory=TrackRefs)
    assumptions: list[Assumption] = Field(default_factory=list)
    unverifiable_requests: list[str] = Field(default_factory=list)
    clarification_question: str | None = None

    @model_validator(mode="after")
    def check_rejection(self):
        if self.action == "reject" and self.moderation.decision != "block":
            raise ValueError("A reject action requires a blocked moderation decision.")
        return self


class PhaseRanking(Model):
    phase_id: str
    order: list[int]


class Rankings(Model):
    phases: list[PhaseRanking]


def apply_interpretation(current, interpretation):
    base = (
        current.model_dump()
        if current is not None and interpretation.action != "create"
        else PlaylistSpec().model_dump()
    )
    changes = interpretation.update.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None:
            continue
        if field == "phases" and base["phases"] and not interpretation.replace_phases:
            by_id = {phase["id"]: phase for phase in base["phases"]}
            for phase in value:
                if phase["id"] in by_id:
                    by_id[phase["id"]].update(phase)
                else:
                    by_id[phase["id"]] = phase
            base[field] = list(by_id.values())
        elif isinstance(value, dict):
            base[field].update(value)
        else:
            base[field] = value
    base["assumptions"] = [item.model_dump() for item in interpretation.assumptions]
    return PlaylistSpec.model_validate(base)


def phase_schedule(spec, total_ms=None):
    phases = spec.phases or [Phase(id="all", description=spec.semantic_brief)]
    target_ms = (
        spec.length.target_duration_s or (spec.length.target_track_count or 14) * 210
    ) * 1000
    total = total_ms if total_ms is not None else target_ms
    durations = [
        phase.duration_s * 1000 if phase.duration_s is not None else None
        for phase in phases
    ]
    missing = durations.count(None)
    given = sum(value or 0 for value in durations)
    if missing:
        remainder = max(missing, total - given)
        durations = [
            remainder / missing if value is None else value for value in durations
        ]
        if sum(durations) > total:
            durations = [total * value / sum(durations) for value in durations]
    elif sum(durations) != total:
        # Preserve short start/end anchors; absorb duration tolerance in the longest phase.
        longest = max(range(len(durations)), key=durations.__getitem__)
        adjusted = durations[longest] + total - sum(durations)
        if adjusted <= 0:
            durations = [total * value / sum(durations) for value in durations]
        else:
            durations[longest] = adjusted
    result, start = [], 0
    for phase, duration in zip(phases, durations):
        result.append((phase, start, start + duration))
        start += duration
    return result
