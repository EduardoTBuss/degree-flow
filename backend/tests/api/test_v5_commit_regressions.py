"""Regressões da auditoria (2026-07-21) do commit de semestre.

(1) `choices` com o MESMO course_code repetido derrubava o commit com 500
    (`AttributeError: 'bool' object has no attribute 'status'` em
    `_autofill_rematricula`, plans.py:328 — o índice `existing` recebia um
    sentinela booleano). Agora a escolha repetida é UMA escolha (last wins) e
    o autofill indexa o próprio request.
(2) O 400 de `section_id` inválido passa a trazer `details` (qual cadeira /
    qual turma), para o cliente conseguir limpar a escolha morta em vez de
    ficar preso num commit que falha para sempre.
"""
from __future__ import annotations

OWN = "22000273"      # obrigatória da grade (curso próprio)
OTHER = "22000270"    # outra obrigatória da grade
TERM_PATH = "2026-1"  # == current_term do plano do seed
SID = f"{TERM_PATH}:{OWN}:T1"


def _plan(client) -> str:
    return client.get("/api/v1/plans").json()["plans"][0]["id"]


def _import_sections(client):
    payload = {"sections": [
        {"course_code": OWN, "label": "T1", "capacity": 40, "enrolled": 5,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
    ]}
    assert client.put(f"/api/v1/admin/terms/{TERM_PATH}/sections", json=payload).status_code == 200


def _commit(client, pid, choices):
    return client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit", json={"choices": choices})


def test_duplicated_choice_does_not_500(client):
    pid = _plan(client)
    _import_sections(client)
    r = _commit(client, pid, [
        {"course_code": OWN, "section_id": SID},
        {"course_code": OWN, "section_id": SID},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    # uma única alocação e um único pedido de rematrícula
    assert body["committed"]["allocated"] == [OWN]
    assert body["committed"]["unchanged"] == []
    camp = body["campaign"]
    assert camp["created"] == [OWN]
    detail = client.get(f"/api/v1/plans/{pid}/campaigns/{camp['campaign_id']}").json()
    assert len([q for q in detail["requests"] if q["course_code"] == OWN]) == 1
    assert len([i for i in body["plan"]["items"] if i["course_code"] == OWN]) == 1


def test_invalid_section_400_carries_details(client):
    pid = _plan(client)
    _import_sections(client)
    r = _commit(client, pid, [
        {"course_code": OWN, "section_id": SID},
        {"course_code": OTHER, "section_id": f"{TERM_PATH}:{OTHER}:MORTA"},
    ])
    assert r.status_code == 400
    details = r.json()["error"]["details"]
    assert details["course_code"] == OTHER
    assert details["section_id"] == f"{TERM_PATH}:{OTHER}:MORTA"
    assert details["reason"] == "not_found"
    # all-or-nothing preservado: nada foi alocado
    assert client.get(f"/api/v1/plans/{pid}").json()["items"] == []
