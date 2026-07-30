"""API contract tests for v2 features (F1 timeline, F3 sections, F2 import)."""
from __future__ import annotations

from tests.pdf_samples import integralizacao_pdf


def _plan(client):
    return client.get("/api/v1/plans").json()["plans"][0]


def test_migration_applied_schema_v2(client):
    # v5 bumped SCHEMA_VERSION to "4" (ARCHITECTURE-v5 section 2 — elective_catalog)
    assert client.get("/api/v1/health").json()["schema_version"] == "4"


# ----- F1 --------------------------------------------------------------


def test_start_term_and_timeline(client):
    p = _plan(client)
    pid = p["id"]
    client.patch(f"/api/v1/plans/{pid}", json={"start_term": "2024/1"})
    client.patch("/api/v1/courses/11100058/state", json={"status": "aprovada", "completed_term": "2024/1"})
    got = client.get(f"/api/v1/plans/{pid}").json()
    assert got["start_term"] == "2024/1"
    assert got["timeline"]["terms"][0] == "2024/1"
    past = {t["term"]: t["course_codes"] for t in got["timeline"]["past_terms"]}
    assert "11100058" in past.get("2024/1", [])


def test_start_term_after_current_is_400(client):
    p = _plan(client)
    r = client.patch(f"/api/v1/plans/{p['id']}", json={"start_term": "2099/1"})
    assert r.status_code == 400


def test_completed_term_cleared_when_not_aprovada(client):
    client.patch("/api/v1/courses/22000299/state", json={"status": "aprovada", "completed_term": "2025/1"})
    r = client.patch("/api/v1/courses/22000299/state", json={"status": "falta"})
    assert r.json()["completed_term"] is None


# ----- F3 --------------------------------------------------------------


def _import_two_conflicting(client, term="2026-2"):
    payload = {"sections": [
        {"course_code": "22000273", "label": "T1", "capacity": 40, "enrolled": 10,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000270", "label": "T1", "capacity": 40, "enrolled": 40,
         "timeslots": [{"weekday": 1, "start": "09:00", "end": "11:00"}]},
    ]}
    return client.put(f"/api/v1/admin/terms/{term}/sections", json=payload)


def test_sections_import_and_list(client):
    r = _import_two_conflicting(client)
    assert r.status_code == 200 and r.json()["upserted"] == 2
    g = client.get("/api/v1/terms/2026-2/sections").json()
    assert g["has_data"] is True and len(g["sections"]) == 2


def test_term_without_data_has_no_sections(client):
    g = client.get("/api/v1/terms/2099-1/sections").json()
    assert g["has_data"] is False and g["sections"] == []


def test_schedule_conflict_is_warning(client):
    _import_two_conflicting(client)
    p = _plan(client)
    pid = p["id"]
    client.put(f"/api/v1/plans/{pid}/items/22000273", json={"term": "2026/2", "section_id": "2026-2:22000273:T1"})
    client.put(f"/api/v1/plans/{pid}/items/22000270", json={"term": "2026/2", "section_id": "2026-2:22000270:T1"})
    sc = client.post(f"/api/v1/plans/{pid}/schedule-check").json()
    conflicts = [d for d in sc["diagnostics"] if d["type"] == "SCHEDULE_CONFLICT"]
    assert conflicts and conflicts[0]["severity"] == "warning"


def test_put_item_rejects_bad_section(client):
    _import_two_conflicting(client)
    p = _plan(client)
    r = client.put(
        f"/api/v1/plans/{p['id']}/items/22000273",
        json={"term": "2026/2", "section_id": "2026-2:22000270:T1"},  # wrong course
    )
    assert r.status_code == 400


def test_recommend_requires_term_with_data(client):
    p = _plan(client)
    r = client.post(f"/api/v1/plans/{p['id']}/recommend-schedule", json={"term": "2099/1"})
    assert r.status_code == 400


# ----- F2 --------------------------------------------------------------


def test_import_historico_pdf_proposal(client):
    pdf = integralizacao_pdf()
    r = client.post(
        "/api/v1/import/historico",
        files={"file": ("integralizacao.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"]["parser"] == "ufpel-integralizacao"
    assert body["inferred"]["start_term"] == "2024/1"
    assert any(m["code"] == "11100058" and m["term"] == "2024/1" for m in body["matches"])


def test_import_historico_scanned_pdf_is_422(client):
    r = client.post(
        "/api/v1/import/historico",
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
    )
    assert r.status_code == 422


def test_import_apply_writes_state(client):
    pdf = integralizacao_pdf()
    proposal = client.post(
        "/api/v1/import/historico",
        files={"file": ("i.pdf", pdf, "application/pdf")},
    ).json()
    r = client.post("/api/v1/import/historico/apply", json={"proposal": proposal})
    assert r.status_code == 200 and r.json()["applied"] > 0
    # 11100058 should now be aprovada with completed_term 2024/1
    cur = client.get("/api/v1/grade-versions/gv-2027-1/curriculum").json()
    by = {c["code"]: c for c in cur["courses"]}
    assert by["11100058"]["status"] == "aprovada"
    assert by["11100058"]["completed_term"] == "2024/1"
