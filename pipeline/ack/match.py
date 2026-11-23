"""Match inbound replies to alert instances.

A reply acks when (a) its sender resolves to an active subscriber
(msisdn last-10-digits or telegram id), (b) its text normalizes to an
ACK word, and (c) the subscriber has an un-acked alert SMS within the
ack window. The MOST RECENT such instance gets the ack.

Anything that doesn't ack stays unmatched and is surfaced by the run
CLI — a reply from a known subscriber that isn't an ack word ("TULONG",
a question) is exactly what a human must read during an event.
"""

import re
from datetime import UTC, datetime, timedelta

from pipeline import config
from pipeline.deliver.store import Store


def norm_msisdn(raw: str | None) -> str:
    """PH numbers arrive as +639..., 639..., 09... — compare on the last
    10 digits (9XXXXXXXXX)."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:]


def is_ack(text: str | None) -> bool:
    if not text:
        return False
    token = re.sub(r"[^A-Za-z]", "", text.strip().split()[0] if text.strip() else "")
    return token.upper() in config.ACK_WORDS


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _find_subscriber(store: Store, channel: str, sender: str | None):
    if channel == "telegram":
        return store.conn.execute(
            "SELECT * FROM subscriber WHERE telegram_id = ? AND active = 1",
            (sender,),
        ).fetchone()
    key = norm_msisdn(sender)
    if not key:
        return None
    for row in store.conn.execute(
        "SELECT * FROM subscriber WHERE msisdn IS NOT NULL AND active = 1"
    ):
        if norm_msisdn(row["msisdn"]) == key:
            return row
    return None


def _open_instance_for(store: Store, subscriber_id: int, now: datetime):
    cutoff = (now - timedelta(hours=config.ACK_WINDOW_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return store.conn.execute(
        """
        SELECT ai.id FROM alert_instance ai
        JOIN delivery_event de ON de.alert_instance_id = ai.id
             AND de.subscriber_id = ? AND de.channel = 'sms'
             AND de.kind IN ('queued', 'sent')
        WHERE ai.created_at >= ?
          AND NOT EXISTS (
            SELECT 1 FROM delivery_event a
            WHERE a.alert_instance_id = ai.id
              AND a.subscriber_id = de.subscriber_id AND a.kind = 'ack'
          )
        ORDER BY ai.created_at DESC LIMIT 1
        """,
        (subscriber_id, cutoff),
    ).fetchone()


def match_inbound(store: Store, now: datetime | None = None) -> dict:
    """Process all unmatched inbound messages; returns summary with the
    replies a human must look at."""
    now = now or datetime.now(UTC)
    summary = {"acked": 0, "needs_human": [], "ignored": 0}

    rows = store.conn.execute(
        "SELECT * FROM inbound_message WHERE matched_alert_instance_id IS NULL"
        " ORDER BY id"
    ).fetchall()
    for msg in rows:
        sub = _find_subscriber(store, msg["channel"], msg["sender"])
        if sub is None:
            summary["ignored"] += 1
            continue
        if not is_ack(msg["text"]):
            summary["needs_human"].append(
                {"from": sub["display_name"], "psgc": sub["psgc"],
                 "channel": msg["channel"], "text": msg["text"]}
            )
            continue
        target = _open_instance_for(store, sub["id"], now)
        if target is None:
            summary["ignored"] += 1  # nothing open to ack (late/duplicate reply)
            continue
        store.record_event(
            target["id"], msg["channel"], "ack", sub["id"],
            {"inbound_id": msg["id"], "text": msg["text"]},
        )
        store.conn.execute(
            "UPDATE inbound_message SET matched_alert_instance_id = ? WHERE id = ?",
            (target["id"], msg["id"]),
        )
        store.conn.commit()
        summary["acked"] += 1
    return summary
