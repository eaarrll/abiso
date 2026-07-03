"""SQLite persistence for delivery + acknowledgment.

Pilot-scale twin of warehouse/schema.sql (the Postgres target): same
tables, same semantics. delivery_event is append-only — it is the
RA 12287 audit timeline; nothing here ever updates or deletes an event.
"""

import json
import sqlite3
from pathlib import Path

from pipeline import config

_DDL = """
CREATE TABLE IF NOT EXISTS subscriber (
    id           INTEGER PRIMARY KEY,
    psgc         TEXT NOT NULL,
    tier         TEXT NOT NULL CHECK (tier IN ('bdrrmc', 'resident')),
    display_name TEXT NOT NULL,
    msisdn       TEXT,
    telegram_id  TEXT,
    language     TEXT NOT NULL DEFAULT 'fil',
    consent_at   TEXT NOT NULL,
    consent_ref  TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS subscriber_psgc ON subscriber (psgc, active);

CREATE TABLE IF NOT EXISTS alert_instance (
    id             INTEGER PRIMARY KEY,
    cap_identifier TEXT NOT NULL,
    psgc           TEXT NOT NULL,
    matched_via    TEXT NOT NULL,
    hazard_type    TEXT,
    max_pct_area   REAL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cap_identifier, psgc)
);

CREATE TABLE IF NOT EXISTS delivery_event (
    id                INTEGER PRIMARY KEY,
    alert_instance_id INTEGER NOT NULL REFERENCES alert_instance(id),
    subscriber_id     INTEGER REFERENCES subscriber(id),
    channel           TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN
        ('queued','sent','delivered','failed','skipped','ack','escalated')),
    detail            TEXT,
    at                TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS delivery_event_instance
    ON delivery_event (alert_instance_id, at);

CREATE TABLE IF NOT EXISTS dispatched_alert (
    cap_identifier TEXT PRIMARY KEY,
    at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inbound_message (
    id          INTEGER PRIMARY KEY,
    channel     TEXT NOT NULL,
    sender      TEXT,
    text        TEXT,
    raw         TEXT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    matched_alert_instance_id INTEGER REFERENCES alert_instance(id)
);
"""


class Store:
    def __init__(self, path: Path | None = None):
        db = path or config.DB_PATH
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)

    # --- subscribers ------------------------------------------------------

    def add_subscriber(
        self,
        psgc: str,
        tier: str,
        display_name: str,
        *,
        msisdn: str | None = None,
        telegram_id: str | None = None,
        language: str = "fil",
        consent_at: str = "",
        consent_ref: str = "",
    ) -> int:
        if not consent_at or not consent_ref:
            raise ValueError(
                "consent_at and consent_ref are required (Data Privacy Act: "
                "opt-in must be documented — when and how it was given)"
            )
        if not msisdn and not telegram_id:
            raise ValueError("subscriber needs at least one channel (msisdn/telegram_id)")
        cur = self.conn.execute(
            "INSERT INTO subscriber (psgc, tier, display_name, msisdn, telegram_id,"
            " language, consent_at, consent_ref) VALUES (?,?,?,?,?,?,?,?)",
            (psgc, tier, display_name, msisdn, telegram_id, language, consent_at, consent_ref),
        )
        self.conn.commit()
        return cur.lastrowid

    def subscribers_for(self, psgc: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM subscriber WHERE psgc = ? AND active = 1", (psgc,)
        ).fetchall()

    def deactivate_subscriber(self, subscriber_id: int) -> None:
        self.conn.execute("UPDATE subscriber SET active = 0 WHERE id = ?", (subscriber_id,))
        self.conn.commit()

    # --- alert instances / events ----------------------------------------

    def get_or_create_instance(
        self, cap_identifier: str, psgc: str, matched_via: str,
        hazard_type: str | None, max_pct_area: float,
    ) -> tuple[int, bool]:
        row = self.conn.execute(
            "SELECT id FROM alert_instance WHERE cap_identifier = ? AND psgc = ?",
            (cap_identifier, psgc),
        ).fetchone()
        if row:
            return row["id"], False
        cur = self.conn.execute(
            "INSERT INTO alert_instance (cap_identifier, psgc, matched_via,"
            " hazard_type, max_pct_area) VALUES (?,?,?,?,?)",
            (cap_identifier, psgc, matched_via, hazard_type, max_pct_area),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def record_event(
        self, alert_instance_id: int, channel: str, kind: str,
        subscriber_id: int | None = None, detail: dict | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO delivery_event (alert_instance_id, subscriber_id,"
            " channel, kind, detail) VALUES (?,?,?,?,?)",
            (alert_instance_id, subscriber_id, channel, kind,
             json.dumps(detail, ensure_ascii=False) if detail else None),
        )
        self.conn.commit()

    def events_for_instance(self, alert_instance_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM delivery_event WHERE alert_instance_id = ? ORDER BY at, id",
            (alert_instance_id,),
        ).fetchall()

    def mark_dispatched(self, cap_identifier: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO dispatched_alert (cap_identifier) VALUES (?)",
            (cap_identifier,),
        )
        self.conn.commit()

    def dispatched_identifiers(self) -> set[str]:
        return {
            r["cap_identifier"]
            for r in self.conn.execute("SELECT cap_identifier FROM dispatched_alert")
        }

    # --- inbound ---------------------------------------------------------

    def record_inbound(
        self, channel: str, sender: str | None, text: str | None, raw: str
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO inbound_message (channel, sender, text, raw) VALUES (?,?,?,?)",
            (channel, sender, text, raw),
        )
        self.conn.commit()
        return cur.lastrowid
