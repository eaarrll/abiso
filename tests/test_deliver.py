from pathlib import Path

import pytest

from pipeline.compose.engine import TemplateBook
from pipeline.deliver.dispatch import dispatch_alert
from pipeline.deliver.store import Store
from pipeline.downscale.fanout import BarangayHit
from pipeline.ingest.cap import parse_cap

FIXTURES = Path(__file__).parent / "fixtures"

PROFILES = {
    "0702219002": {
        "short_name": "Brgy Cansaga",
        "evac_center": "Cansaga Barangay Hall",
        "languages": ["fil", "en"],
    },
    "0702252051": {
        "short_name": "Brgy IV Pob.",
        "evac_center": None,  # not yet collected -> compose must fail closed
        "languages": ["fil", "en"],
    },
}

HITS = [
    BarangayHit("0702219002", "Cansaga", "07022", "polygon", "flood", "3", 0.59),
    BarangayHit("0702252051", "Barangay IV (Pob.)", "07022", "geocode", "flood", "1", 0.63),
]


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "test.db")
    s.add_subscriber(
        "0702219002", "bdrrmc", "Kap. Test", msisdn="+639170000001",
        language="fil", consent_at="2026-07-04T00:00:00Z", consent_ref="test",
    )
    s.add_subscriber(
        "0702219002", "resident", "Res. Test", msisdn="+639170000002",
        language="en", consent_at="2026-07-04T00:00:00Z", consent_ref="test",
    )
    s.add_subscriber(
        "0702252051", "bdrrmc", "Kap. Two", msisdn="+639170000003",
        language="fil", consent_at="2026-07-04T00:00:00Z", consent_ref="test",
    )
    return s


@pytest.fixture(scope="module")
def alert():
    return parse_cap((FIXTURES / "gfa_sample.cap").read_bytes())


def test_consent_is_mandatory(tmp_path):
    s = Store(tmp_path / "c.db")
    with pytest.raises(ValueError, match="consent"):
        s.add_subscriber("0702219002", "bdrrmc", "X", msisdn="+639170000009")


def test_dispatch_dry_run(store, alert, tmp_path):
    summary = dispatch_alert(
        alert, store, PROFILES, TemplateBook(), hits=HITS,
        viber_outbox_dir=tmp_path / "viber",
    )
    assert summary["barangays"] == 2
    # Cansaga: bdrrmc fil + resident en (Extreme passes the resident gate)
    assert summary["sent"] == 2
    # Brgy IV: evac_center missing -> flood_extreme needs {evac} -> per-recipient
    # failure recorded for the SMS and for both broadcast languages
    assert summary["failed"] == 3

    instance = store.conn.execute(
        "SELECT id FROM alert_instance WHERE psgc='0702219002'"
    ).fetchone()
    kinds = [(e["channel"], e["kind"]) for e in store.events_for_instance(instance["id"])]
    assert ("sms", "queued") in kinds       # dry-run records queued, never sent
    assert ("viber", "queued") in kinds
    assert ("sms", "sent") not in kinds

    failed = store.conn.execute(
        "SELECT de.detail FROM delivery_event de JOIN alert_instance ai"
        " ON ai.id = de.alert_instance_id"
        " WHERE ai.psgc='0702252051' AND de.kind='failed'"
    ).fetchall()
    assert failed and "missing slot" in failed[0]["detail"]

    # Viber outbox file is written for the healthy barangay
    files = list((tmp_path / "viber").glob("*_0702219002.txt"))
    assert len(files) == 1
    assert "Cansaga Barangay Hall" in files[0].read_text()


def test_dispatch_is_idempotent(store, alert, tmp_path):
    kw = dict(hits=HITS, viber_outbox_dir=tmp_path / "viber")
    first = dispatch_alert(alert, store, PROFILES, TemplateBook(), **kw)
    second = dispatch_alert(alert, store, PROFILES, TemplateBook(), **kw)
    assert first["sent"] == 2
    assert second["barangays"] == 0 and second["sent"] == 0  # never double-texts


def test_resident_severity_gate(store, tmp_path):
    xml = (FIXTURES / "gfa_sample.cap").read_bytes().replace(
        b"<severity>Extreme</severity>", b"<severity>Moderate</severity>"
    )
    moderate = parse_cap(xml)
    summary = dispatch_alert(
        moderate, store, PROFILES, TemplateBook(),
        hits=HITS[:1], viber_outbox_dir=tmp_path / "viber",
    )
    # flood_watch has no {evac} slot; bdrrmc still gets SMS, resident skipped
    assert summary["sent"] == 1
    assert summary["skipped"] == 1
    row = store.conn.execute(
        "SELECT detail FROM delivery_event WHERE kind='skipped'"
    ).fetchone()
    assert "resident tier" in row["detail"]


def test_inbound_recorded(store):
    mid = store.record_inbound("sms", "+639170000001", "OO", '{"message":"OO"}')
    row = store.conn.execute(
        "SELECT * FROM inbound_message WHERE id=?", (mid,)
    ).fetchone()
    assert row["text"] == "OO" and row["channel"] == "sms"
