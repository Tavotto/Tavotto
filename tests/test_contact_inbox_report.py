"""收件记录的 `kind` / `report_id` 与保存期（ADR 0145 修订 2026-11-02：反馈包回信）。"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import contactinbox

MID = "TVM-ABCD-EFGH-JKMN-PQRS"
MID2 = "TVM-ABCD-EFGH-JKMN-PQRT"
TVD = "TVD-ABCD-EFGH-JKMN-PQRS"
TOKEN = "a" * 64
DAY = 86400


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    contactinbox.reset_for_tests()
    yield
    contactinbox.reset_for_tests()


@pytest.fixture
def clock(monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(contactinbox, "_now", lambda: now[0])
    return now


def _reply(rid="r_1"):
    return [{"id": rid, "created_at": "2026-11-02T00:00:00Z", "body": "hi"}]


def test_old_records_without_kind_load_as_letters():
    contactinbox.path().parent.mkdir(parents=True, exist_ok=True)
    contactinbox.path().write_text(
        json.dumps(
            {
                "version": 1,
                "messages": [
                    {
                        "message_id": MID,
                        "reply_token": TOKEN,
                        "route": "cn",
                        "sent_at": 1,
                        "delivered": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (rec,) = contactinbox._load_locked()["messages"]
    assert rec["kind"] == "letter" and rec["report_id"] == ""


def test_report_kind_requires_a_well_formed_report_id():
    assert not contactinbox.add(MID, TOKEN, "cn", delivered=True, kind="report")
    assert not contactinbox.add(MID, TOKEN, "cn", delivered=True, kind="report", report_id="TVD-x")
    assert not contactinbox.add(MID, TOKEN, "cn", delivered=True, kind="bogus")
    assert contactinbox.public_messages() == []
    assert contactinbox.add(
        MID, TOKEN, "cn", delivered=True, kind="report", report_id=TVD, body="b"
    )
    (pub,) = contactinbox.public_messages()
    assert pub["kind"] == "report" and pub["report_id"] == TVD and pub["body"] == "b"


def test_letters_keep_the_default_shape():
    assert contactinbox.add(MID, TOKEN, "cn", delivered=True, subject="s", body="b")
    (pub,) = contactinbox.public_messages()
    assert pub["kind"] == "letter" and pub["report_id"] == ""
    assert "reply_token" not in json.dumps(pub)


def test_a_report_without_replies_is_dropped_after_30_days_but_a_letter_is_not(clock):
    contactinbox.add(MID, TOKEN, "cn", delivered=True, kind="report", report_id=TVD)
    contactinbox.add(MID2, TOKEN, "cn", delivered=True)
    clock[0] += 29 * DAY
    assert {m["message_id"] for m in contactinbox.public_messages()} == {MID, MID2}
    clock[0] += 2 * DAY
    assert {m["message_id"] for m in contactinbox.public_messages()} == {MID2}


def test_a_report_with_a_reply_lives_365_days(clock):
    contactinbox.add(MID, TOKEN, "cn", delivered=True, kind="report", report_id=TVD)
    clock[0] += 20 * DAY
    contactinbox.apply_replies(MID, "replied", _reply())
    clock[0] += 100 * DAY
    assert [m["message_id"] for m in contactinbox.public_messages()] == [MID]
    clock[0] += 250 * DAY
    assert contactinbox.public_messages() == []


def test_no_records_means_no_poll_targets():
    assert contactinbox.pollable() == []
