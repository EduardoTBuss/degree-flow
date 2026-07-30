"""item 5 — "selecionar semestre" auto-fills the term's enrollment campaign.

On commit, the chosen OWN-COURSE turmas become rematrícula add-requests (status
"desejada"); a FOREIGN-course turma (`section.offered_to` set) is matrícula
especial and is NEVER auto-requested — it is reported in `special_needed` so the
user handles it in the especial round. Correção/especial stay manual.
"""
from __future__ import annotations

OWN = "22000273"      # grade obrigatoria (own course)
FOREIGN = "22000270"  # grade obrigatoria; its turma is marked offered to another course
TERM_PATH = "2026-1"  # == seed plan current_term; endpoint normalizes to 2026/1


def _plan(client) -> str:
    return client.get("/api/v1/plans").json()["plans"][0]["id"]


def _import_sections(client):
    payload = {"sections": [
        {"course_code": OWN, "label": "T1", "capacity": 40, "enrolled": 5,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
        {"course_code": FOREIGN, "label": "T1", "capacity": 40, "enrolled": 5,
         "offered_to": ["ENGENHARIA DE COMPUTAÇÃO"],
         "timeslots": [{"weekday": 2, "start": "08:00", "end": "10:00"}]},
    ]}
    r = client.put(f"/api/v1/admin/terms/{TERM_PATH}/sections", json=payload)
    assert r.status_code == 200, r.text


def _commit(client, pid, choices):
    return client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit", json={"choices": choices})


def test_commit_autofills_rematricula_and_excludes_foreign(client):
    pid = _plan(client)
    _import_sections(client)
    r = _commit(client, pid, [
        {"course_code": OWN, "section_id": f"{TERM_PATH}:{OWN}:T1"},
        {"course_code": FOREIGN, "section_id": f"{TERM_PATH}:{FOREIGN}:T1"},
    ])
    assert r.status_code == 200, r.text
    camp = r.json()["campaign"]
    assert camp["campaign_created"] is True
    assert camp["round"] == "rematricula"          # own-course round (order 1, data)
    assert camp["created"] == [OWN]
    assert camp["special_needed"] == [FOREIGN]     # foreign -> matrícula especial only

    detail = client.get(f"/api/v1/plans/{pid}/campaigns/{camp['campaign_id']}").json()
    reqs = detail["requests"]
    own = [q for q in reqs if q["course_code"] == OWN]
    assert len(own) == 1
    assert own[0]["round"] == "rematricula" and own[0]["kind"] == "add"
    assert own[0]["status"] == "desejada"
    assert own[0]["section_id"] == f"{TERM_PATH}:{OWN}:T1"
    assert all(q["course_code"] != FOREIGN for q in reqs)  # never auto-requested


def test_commit_reuses_campaign_and_is_idempotent(client):
    pid = _plan(client)
    _import_sections(client)
    body = [{"course_code": OWN, "section_id": f"{TERM_PATH}:{OWN}:T1"}]
    _commit(client, pid, body)
    camp = _commit(client, pid, body).json()["campaign"]
    assert camp["campaign_created"] is False   # same (plan, term) campaign reused
    assert camp["created"] == [] and camp["skipped"] == [OWN]


def test_choice_without_turma_creates_no_request(client):
    pid = _plan(client)
    _import_sections(client)
    camp = _commit(client, pid, [{"course_code": OWN, "section_id": None}]).json()["campaign"]
    assert camp["campaign_id"] is None and camp["created"] == []
