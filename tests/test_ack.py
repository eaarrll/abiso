import json
from datetime import UTC, datetime, timedelta

from pipeline.ack.escalate import run_escalations
from pipeline.ack.match import is_ack, match_inbound, norm_msisdn
from pipeline.deliver.store import Store

BASE = datetime(2026, 7, 4, 0, 0, 0, tzinfo=UTC)
PROFILES = {
    "0702219002": {
        "short_name": "Brgy Cansaga",
        "drrmo_msisdn": "+639998887777",
    }
}
ALERT_TEXT = "PAGASA Baha-Extreme Hul2 8PM: Brgy Cansaga: test. Reply OO kung natanggap."


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed(tmp_path, first_send: datetime):
    """Instance with two BDRRMC recipients SMS'd at first_send."""
    store = Store(tmp_path / "ack.db")
    kap = store.add_subscriber(
        "0702219002", "bdrrmc", "Kap. Uno", msisdn="+639170000001",
        consent_at="x", consent_ref="test",
    )
    kag = store.add_subscriber(
        "0702219002", "bdrrmc", "Kag. Dos", msisdn="+639170000002",
        consent_at="x", consent_ref="test",
    )
    iid, _ = store.get_or_create_instance("CAP-1", "0702219002", "geocode",
                                          "flood", 0.6, "Extreme")
    store.conn.execute(
        "UPDATE alert_instance SET created_at = ? WHERE id = ?", (_ts(first_send), iid)
    )
    for sid in (kap, kag):
        store.conn.execute(
            "INSERT INTO delivery_event (alert_instance_id, subscriber_id, channel,"
            " kind, detail, at) VALUES (?,?,?,?,?,?)",
            (iid, sid, "sms", "queued",
             json.dumps({"dry_run": True, "text": ALERT_TEXT}), _ts(first_send)),
        )
    store.conn.commit()
    return store, iid, kap, kag


def test_ack_word_normalization():
    assert is_ack("OO")
    assert is_ack("oo.")
    assert is_ack("Opo po, salamat")
    assert is_ack("YES!")
    assert not is_ack("Tulong! Baha na dito")
    assert not is_ack("")
    assert not is_ack(None)


def test_msisdn_normalization():
    assert norm_msisdn("+639171234567") == norm_msisdn("09171234567")
    assert norm_msisdn("63 917 123 4567") == "9171234567"


def test_reply_acks_and_stops_escalation(tmp_path):
    store, iid, kap, _ = _seed(tmp_path, BASE)
    store.record_inbound("sms", "09170000001", "OO", "{}")  # 0-prefix variant

    m = match_inbound(store, now=BASE + timedelta(minutes=5))
    assert m["acked"] == 1

    kinds = [(e["kind"], e["subscriber_id"]) for e in store.events_for_instance(iid)]
    assert ("ack", kap) in kinds

    # Kap acked, Kag still silent -> escalation still fires for Kag only
    fired = run_escalations(store, PROFILES, now=BASE + timedelta(minutes=12))
    assert [f["step"] for f in fired] == ["resend_sms"]
    resends = [
        e for e in store.events_for_instance(iid)
        if e["channel"] == "sms" and e["detail"] and "resend" in e["detail"]
    ]
    assert len(resends) == 1  # only the silent contact is re-texted


def test_full_ack_means_no_escalation(tmp_path):
    store, iid, kap, kag = _seed(tmp_path, BASE)
    store.record_inbound("sms", "+639170000001", "OO", "{}")
    store.record_inbound("sms", "+639170000002", "opo", "{}")
    assert match_inbound(store, now=BASE + timedelta(minutes=4))["acked"] == 2
    assert run_escalations(store, PROFILES, now=BASE + timedelta(hours=1)) == []


def test_ladder_fires_in_order_one_step_per_run(tmp_path):
    store, iid, *_ = _seed(tmp_path, BASE)
    late = BASE + timedelta(minutes=50)  # all three steps overdue

    steps = []
    for _ in range(4):  # 4th run: ladder exhausted, nothing more fires
        fired = run_escalations(store, PROFILES, now=late)
        steps.extend(f["step"] for f in fired)
    assert steps == ["resend_sms", "notify_alternate", "notify_drrmo"]

    # DRRMO SMS went out and carries the pending names
    drrmo = [
        e for e in store.events_for_instance(iid)
        if e["detail"] and "drrmo" in json.loads(e["detail"] or "{}")
        and json.loads(e["detail"]).get("drrmo")
    ]
    assert len(drrmo) == 1
    assert "Kap. Uno" in json.loads(drrmo[0]["detail"])["text"]


def test_nothing_due_before_first_rung(tmp_path):
    store, *_ = _seed(tmp_path, BASE)
    assert run_escalations(store, PROFILES, now=BASE + timedelta(minutes=5)) == []


def test_non_ack_reply_surfaces_for_human(tmp_path):
    store, *_ = _seed(tmp_path, BASE)
    store.record_inbound("sms", "+639170000001", "Tulong! Lumalalim na", "{}")
    m = match_inbound(store, now=BASE + timedelta(minutes=5))
    assert m["acked"] == 0
    assert m["needs_human"][0]["from"] == "Kap. Uno"
    assert "Tulong" in m["needs_human"][0]["text"]


def test_unknown_sender_ignored(tmp_path):
    store, *_ = _seed(tmp_path, BASE)
    store.record_inbound("sms", "+639990000000", "OO", "{}")
    m = match_inbound(store, now=BASE + timedelta(minutes=5))
    assert m["acked"] == 0 and m["ignored"] == 1


def test_ack_targets_most_recent_instance(tmp_path):
    store, first_iid, kap, _ = _seed(tmp_path, BASE - timedelta(hours=2))
    newer, _ = store.get_or_create_instance("CAP-2", "0702219002", "geocode",
                                            "flood", 0.6, "Severe")
    store.conn.execute(
        "UPDATE alert_instance SET created_at = ? WHERE id = ?", (_ts(BASE), newer)
    )
    store.conn.execute(
        "INSERT INTO delivery_event (alert_instance_id, subscriber_id, channel,"
        " kind, detail, at) VALUES (?,?,?,?,?,?)",
        (newer, kap, "sms", "queued", json.dumps({"text": "t"}), _ts(BASE)),
    )
    store.conn.commit()
    store.record_inbound("sms", "+639170000001", "OO", "{}")
    match_inbound(store, now=BASE + timedelta(minutes=5))
    acks = store.conn.execute(
        "SELECT alert_instance_id FROM delivery_event WHERE kind='ack'"
    ).fetchall()
    assert [a["alert_instance_id"] for a in acks] == [newer]


def test_report_contains_ack_timeline(tmp_path):
    from pipeline.ack.report import build_report

    store, iid, *_ = _seed(tmp_path, BASE)
    store.record_inbound("sms", "+639170000001", "OO", "{}")
    match_inbound(store, now=BASE + timedelta(minutes=8))
    run_escalations(store, PROFILES, now=BASE + timedelta(minutes=12))

    md = build_report(store, "CAP-1", PROFILES)
    assert "BDRRMC ack rate: 1/2 (50%)" in md
    assert "Kap. Uno" in md and "acked in" in md
    assert "Kag. Dos" in md and "NO ACK" in md
    assert "resend_sms" in md
