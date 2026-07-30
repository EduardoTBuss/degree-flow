"""Immutable dataclasses of the engine contract (ARCHITECTURE.md section 8 +
ARCHITECTURE-v2.md section 2.3). No I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# status values
APROVADA = "aprovada"
CURSANDO = "cursando"
FALTA = "falta"
PENDENTE = "pendente"

KIND_OBRIGATORIA = "obrigatoria"
KIND_OPTATIVA = "optativa"
KIND_ATIVIDADE = "atividade"
KIND_FORA = "fora_curriculo"


@dataclass(frozen=True)
class CourseInfo:
    code: str
    name: str
    credits: int
    hours: float
    kind: str  # obrigatoria|optativa|atividade|fora_curriculo
    offer: str  # '/1' | '/2' | 'both'  (effective offer, override already applied)
    prereqs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CurriculumSnapshot:
    grade_version_id: str
    courses: dict[str, CourseInfo]


@dataclass(frozen=True)
class UserState:
    status: dict[str, str]  # code -> aprovada|cursando|falta|pendente (missing = falta)
    difficulty: dict[str, int] = field(default_factory=dict)  # code -> 1..5 (missing = 3)
    completed_term: dict[str, str] = field(default_factory=dict)  # v2: code -> "AAAA/S"

    def status_of(self, code: str) -> str:
        return self.status.get(code, FALTA)

    def difficulty_of(self, code: str) -> int:
        return self.difficulty.get(code, 3)

    def completed_term_of(self, code: str) -> str | None:
        return self.completed_term.get(code)


@dataclass(frozen=True)
class PlanItemSnap:
    term: str
    locked: bool = False
    section_id: str | None = None  # v2: chosen section


@dataclass(frozen=True)
class PlanSnapshot:
    current_term: str
    target_term: str | None
    items: dict[str, PlanItemSnap]
    max_credits_per_term: int = 28
    max_difficulty_per_term: int | None = None
    start_term: str | None = None  # v2


# ----- v2: offer data snapshots -----


@dataclass(frozen=True)
class TimeslotSnap:
    weekday: int
    start_min: int
    end_min: int


@dataclass(frozen=True)
class SectionSnap:
    id: str
    course_code: str
    term: str
    timeslots: tuple[TimeslotSnap, ...]
    label: str | None = None
    professor: str | None = None
    capacity: int | None = None
    enrolled: int | None = None

    def has_vacancy(self) -> bool | None:
        if self.capacity is None or self.enrolled is None:
            return None
        return self.capacity - self.enrolled > 0


@dataclass(frozen=True)
class SectionsSnapshot:
    sections: dict[str, SectionSnap]  # section_id -> SectionSnap
    terms_with_data: frozenset[str] = frozenset()

    def by_course_term(self, code: str, term: str) -> list[SectionSnap]:
        return [s for s in self.sections.values() if s.course_code == code and s.term == term]


@dataclass(frozen=True)
class RequirementCategorySnap:
    key: str
    label: str
    min_hours: float
    counts_courses: bool
    logged_hours: float
    rule_note: str | None = None


@dataclass(frozen=True)
class RequirementsSnapshot:
    categories: tuple[RequirementCategorySnap, ...] = ()


@dataclass(frozen=True)
class Diagnostic:
    id: str
    type: str
    severity: str  # error | warning | info
    course_codes: tuple[str, ...]
    related_codes: tuple[str, ...]
    term: str | None
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "course_codes": list(self.course_codes),
            "related_codes": list(self.related_codes),
            "term": self.term,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class CriticalPath:
    course_codes: tuple[str, ...]
    length_terms: int
    earliest_completion_term: str | None

    def to_dict(self) -> dict:
        return {
            "course_codes": list(self.course_codes),
            "length_terms": self.length_terms,
            "earliest_completion_term": self.earliest_completion_term,
        }


@dataclass(frozen=True)
class TermSummary:
    term: str
    credits: int
    difficulty_sum: int
    course_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "credits": self.credits,
            "difficulty_sum": self.difficulty_sum,
            "course_codes": list(self.course_codes),
        }


@dataclass(frozen=True)
class ValidationResult:
    diagnostics: tuple[Diagnostic, ...]
    blocked_codes: tuple[str, ...]
    critical_path: CriticalPath
    term_summaries: tuple[TermSummary, ...]

    @property
    def valid(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


@dataclass(frozen=True)
class ProposedItem:
    course_code: str
    term: str
    locked: bool

    def to_dict(self) -> dict:
        return {"course_code": self.course_code, "term": self.term, "locked": self.locked}


@dataclass(frozen=True)
class AutoplanResult:
    feasible: bool
    proposal: tuple[ProposedItem, ...]
    diagnostics: tuple[Diagnostic, ...]
    critical_path: CriticalPath
