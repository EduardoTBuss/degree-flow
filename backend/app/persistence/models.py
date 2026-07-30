"""SQLAlchemy 2.0 models — schema per ARCHITECTURE.md section 5.1 + v2 deltas.

v2 additions (ARCHITECTURE-v2.md section 2): course_user_state.completed_term,
plan.start_term, plan_item.section_id, and the new section/timeslot tables
(offer data — a third data class besides catalog and user state).

Indexes beyond PKs (each justified):
- ix_prereq_gv_prereq: reverse lookup "who depends on X" per grade.
- ix_course_mapping_reform_to: translation derived->base groups mappings by to_code.
- ix_requirement_entry_category: entries are always aggregated per category.
- ix_section_term / ix_section_course_term: offer queried per term (dominant read).
- ix_enrollment_campaign_plan / ix_enrollment_request_campaign: campaigns are
  always listed per plan, requests per campaign (v4 C1, ADR-017).
- ix_elective_catalog_kind: the catalog is listed per grade filtered by kind
  (the electives card reads (grade, "optativa") — v5 F1, ADR-027).
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ===== catalog (reimportable) =====


class Reform(Base):
    __tablename__ = "reform"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    effective_term: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    transition_policy: Mapped[str] = mapped_column(Text, nullable=False, default="tbd")


class GradeVersion(Base):
    __tablename__ = "grade_version"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    program: Mapped[str] = mapped_column(Text, nullable=False)
    university: Mapped[str] = mapped_column(Text, nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    derived_from: Mapped[str | None] = mapped_column(ForeignKey("grade_version.id"))
    reform_id: Mapped[str | None] = mapped_column(ForeignKey("reform.id"))
    # v3 (ADR-018): portal code + enrollment round rules live on the grade
    # (1 grade per seed/course); NULL on old dbs -> readers fall back to meta.
    portal_course_code: Mapped[str | None] = mapped_column(Text)
    enrollment_rules: Mapped[str | None] = mapped_column(Text)  # JSON (spec v4 2.3)


class Course(Base):
    __tablename__ = "course"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short: Mapped[str | None] = mapped_column(Text)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)
    default_offer: Mapped[str] = mapped_column(Text, nullable=False, default="both")


class CurriculumEntry(Base):
    __tablename__ = "curriculum_entry"

    grade_version_id: Mapped[str] = mapped_column(
        ForeignKey("grade_version.id"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_term_index: Mapped[int | None] = mapped_column(Integer)


class Prereq(Base):
    __tablename__ = "prereq"
    __table_args__ = (
        Index("ix_prereq_gv_prereq", "grade_version_id", "prereq_code"),
    )

    grade_version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_code: Mapped[str] = mapped_column(Text, primary_key=True)
    prereq_code: Mapped[str] = mapped_column(Text, primary_key=True)


class TransitionRule(Base):
    __tablename__ = "transition_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reform_id: Mapped[str] = mapped_column(ForeignKey("reform.id"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # canonical JSON (4.3)


class CourseMapping(Base):
    __tablename__ = "course_mapping"
    __table_args__ = (
        Index("ix_course_mapping_reform_to", "reform_id", "to_code"),
    )

    reform_id: Mapped[str] = mapped_column(Text, primary_key=True)
    from_code: Mapped[str] = mapped_column(Text, primary_key=True)
    to_code: Mapped[str] = mapped_column(Text, primary_key=True)
    relation: Mapped[str] = mapped_column(Text, nullable=False)


class RequirementCategory(Base):
    __tablename__ = "requirement_category"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    min_hours: Mapped[float] = mapped_column(Float, nullable=False)
    rule_note: Mapped[str | None] = mapped_column(Text)
    counts_courses: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ===== portal-derived catalog (v5 F1a, ADR-025/027) ========================


class ElectiveCatalog(Base):
    """Curriculum-matrix catalog scraped from the portal (`div#curriculo`).

    Per grade because the kind is per grade (PS is obrigatória on 2019/1 and
    optativa on 2027/1). Written ONLY by the scrape (replace per grade, and
    only when the parse produced rows — an empty parse never erases). The
    engine NEVER reads it (presentation/adoption data); it survives seed
    reimport (the importer never touches it). No ON DELETE CASCADE on purpose:
    grade ids are deterministic (`gv-2019-1`) and reimport recreates them."""

    __tablename__ = "elective_catalog"
    __table_args__ = (
        Index("ix_elective_catalog_kind", "grade_version_id", "kind"),
    )

    grade_version_id: Mapped[str] = mapped_column(
        ForeignKey("grade_version.id"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # obrigatoria|optativa|atividade
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)
    prereqs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON array
    portal_kind: Mapped[str | None] = mapped_column(Text)  # raw string — parse audit
    suggested_term_index: Mapped[int | None] = mapped_column(Integer)  # NULL = Optativas
    source: Mapped[str] = mapped_column(Text, nullable=False)  # "scraper:ufpel-curriculo"
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)


class ElectiveAdoption(Base):
    """A catalog elective the user MATERIALIZED into the flowchart (v5 F1b,
    ADR-028). Class: USER STATE — survives seed reimport (the importer never
    writes it), like course_user_state. Never created by a standalone endpoint;
    it is born as the atomic effect of the semester commit
    (POST /plans/{id}/terms/{term}/commit), together with a plan_item in the
    chosen term. No ON DELETE CASCADE from grade_version on purpose: grade ids
    are deterministic (`gv-2019-1`) and reimport recreates them — the adoption
    must outlive that. Read via repos.effective_curriculum_rows (kind=optativa,
    suggested_term_index=None); the engine sees it only through the snapshot."""

    __tablename__ = "elective_adoption"

    grade_version_id: Mapped[str] = mapped_column(
        ForeignKey("grade_version.id"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), primary_key=True)
    adopted_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


# ===== offer data (v2 — third class: reimportable per term, not from seed) =====


class Section(Base):
    __tablename__ = "section"
    __table_args__ = (
        Index("ix_section_term", "term"),
        Index("ix_section_course_term", "course_code", "term"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # "term:code:label"
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    professor: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int | None] = mapped_column(Integer)
    enrolled: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    note: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[str | None] = mapped_column(Text)
    # v3 (ADR-020): JSON array of destination courses; NULL = own course or
    # unknown (level 1 / manual import / pre-v3 data).
    offered_to: Mapped[str | None] = mapped_column(Text)


class Timeslot(Base):
    __tablename__ = "timeslot"
    __table_args__ = (
        Index("ix_timeslot_section", "section_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon..6=Sun
    start_min: Mapped[int] = mapped_column(Integer, nullable=False)  # minutes since 00:00
    end_min: Mapped[int] = mapped_column(Integer, nullable=False)  # exclusive


# ===== user state (survives reimport) =====


class CourseUserState(Base):
    __tablename__ = "course_user_state"

    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="falta")
    offer_override: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    note: Mapped[str | None] = mapped_column(Text)
    completed_term: Mapped[str | None] = mapped_column(Text)  # v2: "AAAA/S" | NULL


class Plan(Base):
    __tablename__ = "plan"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    grade_version_id: Mapped[str] = mapped_column(
        ForeignKey("grade_version.id"), nullable=False
    )
    current_term: Mapped[str] = mapped_column(Text, nullable=False)
    target_term: Mapped[str | None] = mapped_column(Text)
    start_term: Mapped[str | None] = mapped_column(Text)  # v2: "AAAA/S" | NULL
    max_credits_per_term: Mapped[int] = mapped_column(Integer, nullable=False, default=28)
    max_difficulty_per_term: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class PlanItem(Base):
    __tablename__ = "plan_item"

    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plan.id", ondelete="CASCADE"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), primary_key=True)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    section_id: Mapped[str | None] = mapped_column(Text)  # v2: chosen section | NULL


# ===== v4 C1 (ADR-017): enrollment campaign — process log, never allocation ====


class EnrollmentCampaign(Base):
    """One campaign per (plan, term) — D7. Deleting the plan cascades here
    (and from here to the requests): campaigns are per-plan history."""

    __tablename__ = "enrollment_campaign"
    __table_args__ = (
        UniqueConstraint("plan_id", "term", name="uq_enrollment_campaign_plan_term"),
        Index("ix_enrollment_campaign_plan", "plan_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plan.id", ondelete="CASCADE"), nullable=False
    )
    term: Mapped[str] = mapped_column(Text, nullable=False)  # "AAAA/S"
    status: Mapped[str] = mapped_column(Text, nullable=False, default="aberta")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class EnrollmentRequest(Base):
    """One row per intention (ADR-017). `section_id` deliberately has NO FK
    (application semantics, like plan_item.section_id): re-scrape replaces
    sections and the campaign history must survive — `section_label` is the
    snapshot the reader falls back to. Re-requesting a denied section is a NEW
    row in another round (UNIQUE campaign+round+kind+course — D7)."""

    __tablename__ = "enrollment_request"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "round", "kind", "course_code",
            name="uq_enrollment_request_round_kind_course",
        ),
        Index("ix_enrollment_request_campaign", "campaign_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("enrollment_campaign.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[str] = mapped_column(Text, nullable=False)  # key from grade rules
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="add")  # add|drop
    course_code: Mapped[str] = mapped_column(ForeignKey("course.code"), nullable=False)
    section_id: Mapped[str | None] = mapped_column(Text)  # NO FK on purpose (ADR-017)
    section_label: Mapped[str | None] = mapped_column(Text)  # snapshot ("M1")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="desejada")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RequirementEntry(Base):
    __tablename__ = "requirement_entry"
    __table_args__ = (
        Index("ix_requirement_entry_category", "category_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_key: Mapped[str] = mapped_column(
        ForeignKey("requirement_category.key"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)
    entry_date: Mapped[str | None] = mapped_column(Text)


class Meta(Base):
    __tablename__ = "meta"

    k: Mapped[str] = mapped_column(Text, primary_key=True)
    v: Mapped[str] = mapped_column(Text, nullable=False)
