"""Per-alert timeline report — the artifact LGUs need under RA 12287:
which barangays acknowledged, how fast, who never did, what escalated.

usage:
  python -m pipeline.ack.report <cap_identifier>
  python -m pipeline.ack.report --latest
"""

import json
import sys

from pipeline import config
from pipeline.ack.match import parse_ts
from pipeline.deliver.dispatch import load_profiles
from pipeline.deliver.store import Store


def build_report(store: Store, cap_identifier: str, profiles: dict) -> str:
    instances = store.conn.execute(
        "SELECT * FROM alert_instance WHERE cap_identifier = ? ORDER BY psgc",
        (cap_identifier,),
    ).fetchall()
    if not instances:
        raise SystemExit(f"report: no instances for {cap_identifier}")

    lines = [f"# Alert timeline: {cap_identifier}", ""]
    total_bdrrmc, total_acked = 0, 0

    for inst in instances:
        profile = profiles.get(inst["psgc"], {})
        bgy = profile.get("short_name") or inst["psgc"]
        lines += [f"## {bgy} ({inst['psgc']})",
                  f"- severity {inst['severity'] or 'n/a'} · hazard "
                  f"{inst['hazard_type'] or 'n/a'} · dispatched {inst['created_at']}Z"]

        sends, acks, escalations, failures = {}, {}, [], []
        for e in store.events_for_instance(inst["id"]):
            detail = json.loads(e["detail"]) if e["detail"] else {}
            sid = e["subscriber_id"]
            if e["channel"] == "sms" and e["kind"] in ("queued", "sent") and sid \
                    and not detail.get("resend") and not detail.get("drrmo"):
                sends.setdefault(sid, e["at"])
            elif e["kind"] == "ack" and sid:
                acks.setdefault(sid, e["at"])
            elif e["kind"] == "escalated":
                escalations.append(f"{detail.get('step')} at {e['at']}Z "
                                   f"(pending: {detail.get('pending')})")
            elif e["kind"] == "failed":
                failures.append(detail.get("error") or detail.get("reason") or "?")

        for sid, sent_at in sends.items():
            sub = store.conn.execute(
                "SELECT * FROM subscriber WHERE id = ?", (sid,)
            ).fetchone()
            who = f"{sub['display_name']} ({sub['tier']})"
            if sub["tier"] == "bdrrmc":
                total_bdrrmc += 1
            if sid in acks:
                delta = parse_ts(acks[sid]) - parse_ts(sent_at)
                mins = round(delta.total_seconds() / 60, 1)
                lines.append(f"- ✅ {who}: acked in {mins} min")
                if sub["tier"] == "bdrrmc":
                    total_acked += 1
            else:
                lines.append(f"- ⏳ {who}: NO ACK (sent {sent_at}Z)")

        for esc in escalations:
            lines.append(f"- ⚠️ escalated: {esc}")
        for f in failures:
            lines.append(f"- ❌ failed: {f}")
        lines.append("")

    unmatched = store.conn.execute(
        "SELECT * FROM inbound_message WHERE matched_alert_instance_id IS NULL"
        " ORDER BY received_at DESC LIMIT 20"
    ).fetchall()
    if unmatched:
        lines.append("## Unmatched inbound (latest 20 — needs human review)")
        for m in unmatched:
            lines.append(f"- [{m['channel']}] {m['sender']}: {m['text']!r} "
                         f"at {m['received_at']}Z")
        lines.append("")

    if total_bdrrmc:
        pct = round(100 * total_acked / total_bdrrmc)
        lines.insert(2, f"**BDRRMC ack rate: {total_acked}/{total_bdrrmc} ({pct}%)**")
        lines.insert(3, "")
    return "\n".join(lines)


def main() -> None:
    store = Store()
    profiles = load_profiles()
    if len(sys.argv) != 2:
        sys.exit("usage: report <cap_identifier> | report --latest")
    ident = sys.argv[1]
    if ident == "--latest":
        row = store.conn.execute(
            "SELECT cap_identifier FROM alert_instance ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            sys.exit("report: no alert instances yet")
        ident = row["cap_identifier"]

    md = build_report(store, ident, profiles)
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS / f"{ident.replace('/', '_')}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nreport: written to {out}")


if __name__ == "__main__":
    main()
