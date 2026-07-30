"""Pydantic request contracts (validation at the edge)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TERM_PATTERN = r"^\d{4}/[12]$"
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

Status = Literal["aprovada", "cursando", "falta", "pendente"]
Offer = Literal["/1", "/2", "both"]


class CourseStatePatch(BaseModel):
    """All fields optional, at least one required. `offer: null` (explicitly
    sent) resets the override; `completed_term: null` clears the term."""

    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    offer: Offer | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None
    completed_term: str | None = Field(default=None, pattern=TERM_PATTERN)  # v2 (F1)

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    grade_version_id: str | None = None
    current_term: str = Field(pattern=TERM_PATTERN)
    target_term: str | None = Field(default=None, pattern=TERM_PATTERN)
    start_term: str | None = Field(default=None, pattern=TERM_PATTERN)  # v2 (F1)
    max_credits_per_term: int = Field(default=28, ge=1, le=99)
    max_difficulty_per_term: int | None = Field(default=None, ge=1)


class PlanPatch(BaseModel):
    """Partial update; explicit null clears nullable fields."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    grade_version_id: str | None = None
    current_term: str | None = Field(default=None, pattern=TERM_PATTERN)
    target_term: str | None = Field(default=None, pattern=TERM_PATTERN)
    start_term: str | None = Field(default=None, pattern=TERM_PATTERN)  # v2 (F1)
    max_credits_per_term: int | None = Field(default=None, ge=1, le=99)
    max_difficulty_per_term: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class ItemPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(pattern=TERM_PATTERN)
    locked: bool | None = None  # omitted = preserve current lock state
    section_id: str | None = None  # v2 (F3): chosen section; null clears


class AutoplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_term: str | None = Field(default=None, pattern=TERM_PATTERN)
    mode: Literal["preview", "apply"] = "preview"
    respect_existing: bool = False


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    hours: float = Field(gt=0)
    entry_date: str | None = None


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=500)
    hours: float | None = Field(default=None, gt=0)
    entry_date: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


# ===== v2: sections / schedule (F3) =====


class TimeslotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=HHMM_PATTERN)
    end: str = Field(pattern=HHMM_PATTERN)


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    course_code: str
    label: str | None = None
    professor: str | None = None
    capacity: int | None = Field(default=None, ge=0)
    enrolled: int | None = Field(default=None, ge=0)
    note: str | None = None
    # v3 (ADR-020): structured destination courses; additive — old payloads
    # without the field remain valid under extra="forbid".
    offered_to: list[str] | None = None
    timeslots: list[TimeslotIn] = Field(default_factory=list)


class SectionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str | None = Field(default=None, pattern=TERM_PATTERN)  # path wins if given
    sections: list[SectionIn]
    source: str | None = None


class TermChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str = Field(min_length=1)
    section_id: str | None = None  # chosen turma; null = allocate without a section


class TermCommit(BaseModel):
    """v5 F1b (ADR-028): "selecionar semestre" — the term's chosen courses.
    Materializes catalog electives (elective_adoption) and upserts plan_item."""
    model_config = ConfigDict(extra="forbid")

    choices: list[TermChoice] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(pattern=TERM_PATTERN)
    max_credits: int | None = Field(default=None, ge=1)
    max_difficulty: int | None = Field(default=None, ge=1)
    locked_choices: list[str] = Field(default_factory=list)
    top_n: int = Field(default=3, ge=1, le=10)
    # v5 F2a (ADR-029): pin by COURSE — the engine picks the section.
    # Empty/absent => the exact pre-v5 recommend_schedule path.
    pinned_courses: list[str] = Field(default_factory=list)


class ScheduleCheckRequest(BaseModel):
    """Optional body of POST /plans/{id}/schedule-check. `extra_section_ids`
    are sections NOT yet in the plan (pending elective picks in Horários) that
    must be conflict-checked alongside the plan's items (item 4). Absent/empty
    body => the exact pre-existing behaviour (plan items only)."""

    model_config = ConfigDict(extra="forbid")

    extra_section_ids: list[str] = Field(default_factory=list)


# ===== v4 C1: enrollment campaigns (ADR-017) =====


class CampaignCreate(BaseModel):
    """`term` is validated in the router (spec 6.3 wants 400 BAD_REQUEST for a
    malformed/past term, not a 422), and defaults to next_term(current_term)."""

    model_config = ConfigDict(extra="forbid")

    term: str | None = None


class CampaignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["aberta", "encerrada"] | None = None
    note: str | None = None  # explicit null clears

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class EnrollmentRequestCreate(BaseModel):
    """`round` is free text on purpose: valid keys come from the grade's
    enrollment_rules (ADR-018), never from code — the router validates."""

    model_config = ConfigDict(extra="forbid")

    round: str = Field(min_length=1, max_length=50)
    kind: Literal["add", "drop"] = "add"
    course_code: str = Field(min_length=1, max_length=50)
    section_id: str | None = None


class EnrollmentRequestPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["desejada", "pedida", "aceita", "negada"] | None = None
    section_id: str | None = None  # explicit null clears (non-terminal only)
    note: str | None = None  # editable in any state (D4)

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


# ===== v2: PDF import apply (F2) =====


class ImportApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: dict  # the (edited) ImportProposal returned by /import/historico
    set_plan_start_term: dict | None = None  # {"plan_id": "..."} | null


# ===== C2: swap suggestions + what-if (spec v4 6.6, ADR-024) =====


class SwapSuggestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_suggestions: int = Field(default=5, ge=1, le=20)


class WhatIfAssumption(BaseModel):
    """`outcome` stays a plain string on purpose: spec 6.6 wants
    400 BAD_REQUEST for an invalid outcome (not a 422) — the router
    validates against {aceita, negada}."""

    model_config = ConfigDict(extra="forbid")

    request_id: int
    outcome: str = Field(min_length=1, max_length=20)


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assume: list[WhatIfAssumption]
