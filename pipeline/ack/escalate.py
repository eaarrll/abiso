"""Escalation ladder on BDRRMC silence (PLAN.md §2, the differentiator).

For every alert instance inside the escalation window whose BDRRMC
recipients have not all acked, fire the highest DUE step not yet done —
at most one step per instance per run (cron every ~5 min), so a stalled
loop catching up never machine-guns a barangay captain:

  10 min  resend_sms        same message again to every silent contact
  25 min  notify_alternate  operator task: call them / send someone
  40 min  notify_drrmo      SMS the municipal DRRMO contact (profile)

Every step is recorded as an 'escalated' delivery_event — silence has a
paper trail.
"""

import json
from datetime import UTC, datetime, timedelta

from pipeline import config
from pipeline.ack.match import parse_ts
from pipeline.deliver import channels
from pipeline.deliver.store import Store


def _bdrrmc_state(store: Store, instance_id: int) -> dict:
    """Who got the SMS, who acked, when the first send happened, which
    escalation steps already fired."""
    sent_to, acked, first_send, steps_done, last_text = {}, set(), None, set(), {}
    for e in store.events_for_instance(instance_id):
        detail = json.loads(e["detail"]) if e["detail"] else {}
        if e["channel"] == "sms" and e["kind"] in ("queued", "sent") and e["subscriber_id"]:
            sub = store.conn.execute(
                "SELECT * FROM subscriber WHERE id = ?", (e["subscriber_id"],)
            ).fetchone()
            if sub and sub["tier"] == "bdrrmc":
                sent_to[sub["id"]] = sub
                if "text" in detail:
                    last_text[sub["id"]] = detail["text"]
                ts = parse_ts(e["at"])
                if first_send is None or ts < first_send:
                    first_send = ts
        elif e["kind"] == "ack" and e["subscriber_id"]:
            acked.add(e["subscriber_id"])
        elif e["kind"] == "escalated":
            steps_done.add(detail.get("step"))
    return {
        "sent_to": sent_to,
        "pending": {sid: s for sid, s in sent_to.items() if sid not in acked},
        "first_send": first_send,
        "steps_done": steps_done,
        "last_text": last_text,
    }


def _fire(store: Store, instance, state, step: str, profiles: dict) -> None:
    pending = state["pending"]
    names = ", ".join(s["display_name"] for s in pending.values())
    profile = profiles.get(instance["psgc"], {})
    bgy = profile.get("short_name") or instance["psgc"]

    if step == "resend_sms":
        for sid, sub in pending.items():
            text = state["last_text"].get(sid)
            if not text:
                continue
            res = channels.send_sms(sub["msisdn"], text)
            store.record_event(instance["id"], "sms", res.kind, sid,
                               {**res.detail, "text": text, "resend": True})
    elif step == "notify_alternate":
        print(f"ESCALATION [{bgy}] no ack from: {names} — "
              f"CALL them or send someone (alert {instance['cap_identifier']})")
    elif step == "notify_drrmo":
        drrmo = profile.get("drrmo_msisdn")
        text = (f"ABISO ESCALATION: {bgy} BDRRMC has not acknowledged alert "
                f"{instance['cap_identifier']} ({instance['severity']}). "
                f"No reply from: {names}. Please verify they received the warning.")
        if drrmo:
            res = channels.send_sms(drrmo, text)
            store.record_event(instance["id"], "sms", res.kind, None,
                               {**res.detail, "text": text, "drrmo": True})
        else:
            print(f"ESCALATION [{bgy}] wants DRRMO notify but no drrmo_msisdn "
                  f"in profile — operator must act: {text}")

    store.record_event(instance["id"], "escalation", "escalated", None,
                       {"step": step, "pending": names})


def run_escalations(
    store: Store, profiles: dict, now: datetime | None = None
) -> list[dict]:
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(hours=config.ESCALATION_WINDOW_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    fired = []
    instances = store.conn.execute(
        "SELECT * FROM alert_instance WHERE created_at >= ?", (cutoff,)
    ).fetchall()
    for instance in instances:
        state = _bdrrmc_state(store, instance["id"])
        if not state["pending"] or state["first_send"] is None:
            continue
        elapsed_min = (now - state["first_send"]).total_seconds() / 60
        due = [
            step for minutes, step in config.ESCALATION_LADDER
            if elapsed_min >= minutes and step not in state["steps_done"]
        ]
        if not due:
            continue
        step = due[0]  # one step per run; catch-up never machine-guns
        _fire(store, instance, state, step, profiles)
        fired.append({"instance": instance["id"], "psgc": instance["psgc"],
                      "step": step, "elapsed_min": round(elapsed_min, 1)})
    return fired
