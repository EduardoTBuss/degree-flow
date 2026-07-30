"""Seed importer + generic grade derivation (ADR-003).

Idempotent: catalog is upserted by natural key; user state and plans are only
seeded on the very first load and survive every reimport. Reforms are applied
GENERICALLY from the seed's declarative rules (merge/swap/add/remove) — no
course code is hard-coded here.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import SCHEMA_VERSION
from ..persistence.models import (
    Course,
    CourseMapping,
    CourseUserState,
    CurriculumEntry,
    GradeVersion,
    Meta,
    Plan,
    Prereq,
    Reform,
    RequirementCategory,
    TransitionRule,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_gv(label: str) -> str:
    return "gv-" + label.replace("/", "-")


def get_meta(session: Session, key: str) -> str | None:
    row = session.get(Meta, key)
    return row.v if row else None


def set_meta(session: Session, key: str, value: str) -> None:
    row = session.get(Meta, key)
    if row:
        row.v = value
    else:
        session.add(Meta(k=key, v=value))


def sync_portal_meta(session: Session, seed_meta: dict) -> None:
    """ADR-012: upsert meta['portal_course_code'] from seed meta.codigo_curso.

    Runs on EVERY startup (even when seed_hash is unchanged) so existing
    databases gain the key without a forced reimport. Missing in seed = no-op
    (the scrape endpoint answers 400 PORTAL_CODE_MISSING, never crashes).
    v4: kept alive as the read fallback of ADR-018 (old dbs, /health)."""
    code = seed_meta.get("codigo_curso")
    if code:
        set_meta(session, "portal_course_code", str(code))


# ADR-018: institutional default (UFPel/Cobalto) used when the seed has no
# meta.regras_matricula block. This is a DATA constant and it lives in the
# importer on purpose — importers may be course/institution specific (v4
# principle 4); domain code only ever reads grade_version.enrollment_rules.
DEFAULT_ENROLLMENT_RULES: dict = {
    "rodadas": [
        {
            "key": "rematricula",
            "label": "Rematrícula",
            "order": 1,
            "scope": "proprio_curso",
            "max_adds": None,
            "allows_drops": False,
        },
        {
            "key": "correcao",
            "label": "Correção de matrícula",
            "order": 2,
            "scope": "proprio_curso",
            "max_adds": None,
            "allows_drops": True,
            "drops_before_adds": True,
        },
        {
            "key": "especial",
            "label": "Matrícula especial",
            "order": 3,
            "scope": "qualquer_curso",
            "max_adds": 2,
            "allows_drops": False,
            "note": "sujeita à aprovação dos dois coordenadores",
        },
    ]
}


def sync_grade_meta(session: Session, seed_meta: dict) -> None:
    """ADR-018: upsert grade_version.portal_course_code / .enrollment_rules.

    Runs on EVERY startup (same pattern as sync_portal_meta) so a db migrated
    in place (v2/v3 columns added by run_migrations) gains the data without a
    forced reimport. One seed = one course: every grade version (base and
    derived) receives the same values. Seed without meta.codigo_curso leaves
    portal_course_code NULL (readers fall back to meta, then 400); seed
    without meta.regras_matricula gets the institutional default above."""
    code = seed_meta.get("codigo_curso")
    rules = seed_meta.get("regras_matricula") or DEFAULT_ENROLLMENT_RULES
    rules_json = json.dumps(rules, ensure_ascii=False)
    for gv in session.execute(select(GradeVersion)).scalars():
        if code:
            gv.portal_course_code = str(code)
        gv.enrollment_rules = rules_json


def _current_term_from_date(iso_date: str | None) -> str:
    """Semester in progress on the given date (month <= 7 -> /1)."""
    dt = datetime.fromisoformat(iso_date) if iso_date else datetime.now(timezone.utc)
    return f"{dt.year}/{1 if dt.month <= 7 else 2}"


# ---------- rule normalization (seed pt-BR keys -> canonical 4.3 payload) ----------


def normalize_rule(raw: dict, seed_courses: dict[str, dict]) -> dict:
    rtype = raw.get("type") or raw.get("tipo")
    if rtype == "merge":
        frm = raw.get("from") or raw.get("de") or []
        to = raw.get("to")
        if not isinstance(to, dict):
            to = {
                "code": "MERGE:" + "+".join(frm),
                "name": raw.get("para") or raw.get("to") or "Cadeira unificada",
                "credits": raw.get("creditos") or raw.get("credits"),
                "hours": raw.get("hours") or raw.get("horas"),
            }
        return {
            "type": "merge",
            "from": list(frm),
            "to": to,
            "prereqs": raw.get("prereqs", "union_external"),
            "completion": raw.get("completion", "all_or_retake"),
        }
    if rtype == "swap":
        if "changes" in raw:
            return {"type": "swap", "changes": raw["changes"]}
        changes = []
        out_code = raw.get("sai_obrigatoria")
        in_code = raw.get("entra_obrigatoria")
        out_term_index = None
        if out_code and out_code in seed_courses:
            out_term_index = seed_courses[out_code].get("sem_sugerido")
        if out_code:
            changes.append({"code": out_code, "kind": raw.get("vira", "optativa")})
        if in_code:
            changes.append(
                {
                    "code": in_code,
                    "kind": "obrigatoria",
                    "suggested_term_index": out_term_index,
                }
            )
        return {"type": "swap", "changes": changes}
    if rtype == "add":
        return {
            "type": "add",
            "course": raw.get("course") or raw.get("cadeira"),
            "kind": raw.get("kind") or raw.get("tipo_entrada", "obrigatoria"),
            "prereqs": raw.get("prereqs", []),
            "suggested_term_index": raw.get("suggested_term_index")
            or raw.get("sem_sugerido"),
        }
    if rtype == "remove":
        return {"type": "remove", "code": raw.get("code") or raw.get("codigo")}
    raise ValueError(f"Unknown transition rule type: {rtype!r}")


# ---------- derivation (materializes the derived GradeVersion) ----------


def derive_grade(
    session: Session,
    base_id: str,
    reform: Reform,
    rules: list[dict],
    program: str,
    university: str,
) -> str:
    gv_id = slug_gv(reform.effective_term)

    gv = session.get(GradeVersion, gv_id)
    if gv is None:
        gv = GradeVersion(id=gv_id)
        session.add(gv)
    gv.label = reform.effective_term
    gv.program = program
    gv.university = university
    gv.is_base = False
    gv.derived_from = base_id
    gv.reform_id = reform.id

    # wipe previously materialized rows (idempotent regeneration)
    session.execute(delete(CurriculumEntry).where(CurriculumEntry.grade_version_id == gv_id))
    session.execute(delete(Prereq).where(Prereq.grade_version_id == gv_id))
    session.execute(delete(CourseMapping).where(CourseMapping.reform_id == reform.id))
    session.flush()

    # in-memory copy of the base grade
    entries: dict[str, dict] = {}
    for e in session.execute(
        select(CurriculumEntry).where(CurriculumEntry.grade_version_id == base_id)
    ).scalars():
        entries[e.course_code] = {
            "kind": e.kind,
            "suggested_term_index": e.suggested_term_index,
        }
    prereqs: dict[str, set[str]] = {code: set() for code in entries}
    for p in session.execute(
        select(Prereq).where(Prereq.grade_version_id == base_id)
    ).scalars():
        prereqs.setdefault(p.course_code, set()).add(p.prereq_code)

    mappings: list[tuple[str, str, str]] = []  # (from, to, relation)
    touched: set[str] = set()

    for payload in rules:
        rtype = payload["type"]
        if rtype == "merge":
            origins = payload["from"]
            to = payload["to"]
            code = to["code"]
            _upsert_course(
                session,
                code=code,
                name=to.get("name") or code,
                short=to.get("short"),
                credits=int(to.get("credits") or 0),
                hours=float(to.get("hours") or 0),
                default_offer=to.get("offer", "both"),
            )
            origin_entries = [entries[o] for o in origins if o in entries]
            kind = origin_entries[0]["kind"] if origin_entries else "obrigatoria"
            stis = [
                e["suggested_term_index"]
                for e in origin_entries
                if e["suggested_term_index"] is not None
            ]
            union_prereqs: set[str] = set()
            if payload.get("prereqs", "union_external") == "union_external":
                for o in origins:
                    union_prereqs |= prereqs.get(o, set())
                union_prereqs -= set(origins)
            for o in origins:
                entries.pop(o, None)
                prereqs.pop(o, None)
            entries[code] = {
                "kind": kind,
                "suggested_term_index": min(stis) if stis else None,
            }
            prereqs[code] = union_prereqs
            # rewrite edges that pointed to any origin
            origin_set = set(origins)
            for c, ps in prereqs.items():
                if c != code and ps & origin_set:
                    ps -= origin_set
                    ps.add(code)
            mappings.extend((o, code, "merged_into") for o in origins)
            touched |= origin_set | {code}

        elif rtype == "swap":
            for change in payload.get("changes", []):
                c = change["code"]
                if c not in entries:
                    continue
                entries[c]["kind"] = change["kind"]
                if change.get("suggested_term_index") is not None:
                    entries[c]["suggested_term_index"] = change["suggested_term_index"]
                mappings.append((c, c, "kind_changed"))
                touched.add(c)

        elif rtype == "add":
            course = payload["course"]
            code = course["code"]
            _upsert_course(
                session,
                code=code,
                name=course.get("name") or code,
                short=course.get("short"),
                credits=int(course.get("credits") or 0),
                hours=float(course.get("hours") or 0),
                default_offer=course.get("offer", "both"),
            )
            entries[code] = {
                "kind": payload.get("kind", "obrigatoria"),
                "suggested_term_index": payload.get("suggested_term_index"),
            }
            prereqs[code] = set(payload.get("prereqs") or [])
            touched.add(code)

        elif rtype == "remove":
            code = payload["code"]
            entries.pop(code, None)
            prereqs.pop(code, None)
            for ps in prereqs.values():
                ps.discard(code)
            touched.add(code)

    # provenance for untouched courses
    for code in entries:
        if code not in touched:
            mappings.append((code, code, "carried"))

    # persist materialized grade
    for code, e in entries.items():
        session.add(
            CurriculumEntry(
                grade_version_id=gv_id,
                course_code=code,
                kind=e["kind"],
                suggested_term_index=e["suggested_term_index"],
            )
        )
    for code, ps in prereqs.items():
        if code not in entries:
            continue
        for p in sorted(ps):
            session.add(
                Prereq(grade_version_id=gv_id, course_code=code, prereq_code=p)
            )
    seen = set()
    for from_code, to_code, relation in mappings:
        key = (from_code, to_code)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            CourseMapping(
                reform_id=reform.id,
                from_code=from_code,
                to_code=to_code,
                relation=relation,
            )
        )
    session.flush()
    return gv_id


def _upsert_course(
    session: Session,
    code: str,
    name: str,
    short: str | None,
    credits: int,
    hours: float,
    default_offer: str,
) -> Course:
    course = session.get(Course, code)
    if course is None:
        course = Course(code=code)
        session.add(course)
    course.name = name
    course.short = short
    course.credits = credits
    course.hours = hours
    course.default_offer = default_offer
    return course


# ---------- main import ----------


def import_seed(session: Session, seed_file: Path, force: bool = False) -> dict:
    raw = seed_file.read_bytes()
    seed_hash = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw.decode("utf-8"))
    meta = data["meta"]
    if not force and get_meta(session, "seed_hash") == seed_hash:
        # ADR-012/ADR-018: cheap idempotent upserts even without a full
        # reimport; schema_version reflects run_migrations (already applied
        # by init_db before we get here), so a migrated db reports "3".
        sync_portal_meta(session, meta)
        sync_grade_meta(session, meta)
        set_meta(session, "schema_version", SCHEMA_VERSION)
        session.commit()
        return {"imported": False, "reason": "seed unchanged", "seed_hash": seed_hash}

    seed_courses = {c["code"]: c for c in data["courses"]}

    # reforms first (FK from grade_version)
    reforms: list[tuple[Reform, list[dict]]] = []
    for r in data.get("reformas", []):
        reform = session.get(Reform, r["id"])
        if reform is None:
            reform = Reform(id=r["id"])
            session.add(reform)
        reform.effective_term = r.get("vigencia") or r.get("effective_term")
        reform.description = r.get("descricao") or r.get("description")
        # D2: reforma 2027/1 confirmed as 'optional'. The seed has no structured
        # field yet, so 'optional' is the importer default (divergence noted).
        reform.transition_policy = (
            r.get("transition_policy") or r.get("politica_transicao") or "optional"
        )
        session.execute(
            delete(TransitionRule).where(TransitionRule.reform_id == reform.id)
        )
        session.flush()
        rules = [
            normalize_rule(rule, seed_courses)
            for rule in (r.get("regras") or r.get("rules") or [])
        ]
        for rule in rules:
            session.add(
                TransitionRule(
                    reform_id=reform.id,
                    type=rule["type"],
                    payload=json.dumps(rule, ensure_ascii=False),
                )
            )
        reforms.append((reform, rules))

    # base grade version
    base_label = meta["versao_grade"]
    base_id = slug_gv(base_label)
    base = session.get(GradeVersion, base_id)
    if base is None:
        base = GradeVersion(id=base_id)
        session.add(base)
    base.label = base_label
    base.program = meta.get("curso", "")
    base.university = meta.get("universidade", "")
    base.is_base = True
    base.derived_from = None
    base.reform_id = None

    # courses + base curriculum + base prereqs
    session.execute(delete(CurriculumEntry).where(CurriculumEntry.grade_version_id == base_id))
    session.execute(delete(Prereq).where(Prereq.grade_version_id == base_id))
    session.flush()
    for c in data["courses"]:
        _upsert_course(
            session,
            code=c["code"],
            name=c["name"],
            short=c.get("short"),
            credits=int(c["credits"]),
            hours=float(c["hours"]),
            default_offer=c.get("offer", "both"),
        )
        session.add(
            CurriculumEntry(
                grade_version_id=base_id,
                course_code=c["code"],
                kind=c["type"],
                suggested_term_index=c.get("sem_sugerido"),
            )
        )
        for p in c.get("prereqs", []):
            session.add(
                Prereq(grade_version_id=base_id, course_code=c["code"], prereq_code=p)
            )
    session.flush()

    # requirement categories (RN6) — generic RequirementCategory rows
    req = data.get("requisitos_integralizacao", {})
    categories = [
        (
            "complementares",
            "Atividades Complementares",
            req.get("atividades_complementares_horas_min"),
            req.get("atividades_complementares_regra"),
            False,
        ),
        ("optativas", "Optativas", req.get("optativas_horas_min"), None, True),
        ("livres", "Atividades Livres", req.get("atividades_livres_horas_min"), None, False),
    ]
    for key, label, min_hours, rule_note, counts in categories:
        if min_hours is None:
            continue
        cat = session.get(RequirementCategory, key)
        if cat is None:
            cat = RequirementCategory(key=key)
            session.add(cat)
        cat.label = label
        cat.min_hours = float(min_hours)
        cat.rule_note = rule_note
        cat.counts_courses = counts

    # derive materialized grades for each reform
    derived_ids: list[str] = []
    for reform, rules in reforms:
        derived_ids.append(
            derive_grade(
                session,
                base_id,
                reform,
                rules,
                program=base.program,
                university=base.university,
            )
        )

    # exactly one default grade: seed's meta.grade_default, else latest derived
    default_label = meta.get("grade_default")
    default_id = slug_gv(default_label) if default_label else (
        derived_ids[-1] if derived_ids else base_id
    )
    for gv in session.execute(select(GradeVersion)).scalars():
        gv.is_default = gv.id == default_id

    # first load only: bootstrap user state + initial plan (D2/D3)
    first_load = get_meta(session, "first_import_done") != "1"
    if first_load:
        for c in data["courses"]:
            if session.get(CourseUserState, c["code"]) is None:
                session.add(
                    CourseUserState(
                        course_code=c["code"],
                        status=c.get("status", "falta"),
                        note=c.get("nota"),
                    )
                )
        current_term = _current_term_from_date(meta.get("base_integralizacao"))
        now = _now()
        session.add(
            Plan(
                id=str(uuid.uuid4()),
                name="My plan",
                grade_version_id=default_id,
                current_term=current_term,
                target_term=None,
                max_credits_per_term=28,
                max_difficulty_per_term=None,
                created_at=now,
                updated_at=now,
            )
        )

    sync_portal_meta(session, meta)  # ADR-012
    sync_grade_meta(session, meta)  # ADR-018 (after all grade versions exist)
    set_meta(session, "seed_hash", seed_hash)
    set_meta(session, "schema_version", SCHEMA_VERSION)
    set_meta(session, "last_import", _now())
    set_meta(session, "first_import_done", "1")
    session.commit()

    return {
        "imported": True,
        "seed_hash": seed_hash,
        "base_grade": base_id,
        "derived_grades": derived_ids,
        "default_grade": default_id,
        "first_load": first_load,
    }
