"""Dispatch: CAP alert -> subscribed barangays -> composed messages ->
channels -> delivery_event audit rows.

Failure semantics:
- per-recipient fail-closed: a ComposeError (missing evac center,
  unapproved language) records a 'failed' event and moves on — one bad
  profile never blocks the rest of a barangay, and the failure is in the
  audit trail for the ack loop to escalate
- idempotent: (cap_identifier, psgc) that already has delivery events is
  skipped, so re-running after a crash or double-poll never double-texts
- unresolved CAP areas exit non-zero — a human must look

usage:
  python -m pipeline.deliver.dispatch <alert.xml> [...]
  python -m pipeline.deliver.dispatch --new     # everything fetched but undispatched
"""

import sys

import yaml

from pipeline import config
from pipeline.compose.engine import ComposeError, TemplateBook
from pipeline.deliver import channels
from pipeline.deliver.store import Store
from pipeline.downscale.fanout import BarangayHit, Downscaler
from pipeline.ingest.cap import CapAlert, parse_cap


def load_profiles() -> dict:
    path = config.REFERENCE / "pilot_barangays.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def dispatch_alert(
    alert: CapAlert,
    store: Store,
    profiles: dict,
    book: TemplateBook | None = None,
    hits: list[BarangayHit] | None = None,
    viber_outbox_dir=None,
) -> dict:
    """Returns a summary dict; raises nothing per-recipient (see module doc)."""
    book = book or TemplateBook()
    template = book.select(alert)
    severity = alert.infos[0].severity if alert.infos else ""

    if hits is None:
        result = Downscaler().fan_out(alert)
        hits = result.hits
        unresolved = result.unresolved
    else:
        unresolved = []

    summary = {"alert": alert.identifier, "template": template["id"],
               "barangays": 0, "sent": 0, "failed": 0, "skipped": 0,
               "unresolved": unresolved}

    for hit in hits:
        profile = profiles.get(hit.psgc)
        if profile is None:
            continue  # not a subscribed barangay
        instance_id, created = store.get_or_create_instance(
            alert.identifier, hit.psgc, hit.matched_via, hit.hazard_type, hit.max_pct_area
        )
        if not created and store.events_for_instance(instance_id):
            print(f"dispatch: {hit.psgc} already handled for {alert.identifier}, skipping")
            continue
        summary["barangays"] += 1

        slots = {"bgy": profile.get("short_name") or f"Brgy {hit.name}"}
        if profile.get("evac_center"):
            slots["evac"] = profile["evac_center"]
        languages = profile.get("languages") or ["fil"]

        # SMS per subscriber, in their language, ack line for BDRRMC tier.
        for sub in store.subscribers_for(hit.psgc):
            if sub["tier"] == "resident" and severity not in config.RESIDENT_SMS_SEVERITIES:
                store.record_event(instance_id, "sms", "skipped", sub["id"],
                                   {"reason": f"resident tier, severity={severity}"})
                summary["skipped"] += 1
                continue
            if not sub["msisdn"]:
                continue
            try:
                rendered = book.render(
                    alert, template, sub["language"], channel="sms",
                    slots=slots, tier=sub["tier"],
                )
            except ComposeError as e:
                store.record_event(instance_id, "sms", "failed", sub["id"],
                                   {"error": str(e)})
                summary["failed"] += 1
                continue
            res = channels.send_sms(sub["msisdn"], rendered.text)
            store.record_event(instance_id, "sms", res.kind, sub["id"],
                               {**res.detail, "segments": rendered.segments,
                                "language": rendered.language})
            summary["sent" if res.ok else "failed"] += 1

        # Broadcast channels: all profile languages stacked in one post.
        broadcast_texts = []
        for lang in languages:
            try:
                r = book.render(alert, template, lang, channel="rich",
                                slots=slots, tier="resident")
                broadcast_texts.append(r.text)
            except ComposeError as e:
                store.record_event(instance_id, "broadcast", "failed", None,
                                   {"language": lang, "error": str(e)})
                summary["failed"] += 1
        if broadcast_texts:
            post = "\n\n".join(broadcast_texts)
            if profile.get("telegram_chat_id"):
                res = channels.post_telegram(str(profile["telegram_chat_id"]), post)
                store.record_event(instance_id, "telegram", res.kind, None, res.detail)
            res = channels.viber_outbox(alert.identifier, hit.psgc, post,
                                        outbox=viber_outbox_dir)
            store.record_event(instance_id, "viber", res.kind, None, res.detail)

    return summary


def _undispatched_paths(store: Store) -> list:
    raw_dir = config.CAP_DIR / "raw"
    if not raw_dir.exists():
        return []
    done = store.dispatched_identifiers()
    paths = []
    for p in sorted(raw_dir.glob("*.xml")):
        alert = parse_cap(p.read_bytes())
        if alert.identifier not in done:
            paths.append(p)
    return paths


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: dispatch <alert.xml> [...] | dispatch --new")
    store = Store()
    profiles = load_profiles()
    if not profiles:
        sys.exit("dispatch: no pilot barangays in reference/pilot_barangays.yaml")
    book = TemplateBook()

    paths = _undispatched_paths(store) if args == ["--new"] else args
    if not paths:
        print("dispatch: nothing new")
        return

    any_unresolved = False
    for p in paths:
        alert = parse_cap(open(p, "rb").read())
        try:
            s = dispatch_alert(alert, store, profiles, book)
        except ComposeError as e:
            print(f"dispatch: {p}: REFUSED — {e}")
            any_unresolved = True
            continue
        store.mark_dispatched(alert.identifier)
        print(f"dispatch: {s['alert']} [{s['template']}] barangays={s['barangays']} "
              f"sent={s['sent']} failed={s['failed']} skipped={s['skipped']}")
        if s["unresolved"]:
            print(f"dispatch: UNRESOLVED areas need a human: {s['unresolved']}")
            any_unresolved = True
    if any_unresolved:
        sys.exit(2)


if __name__ == "__main__":
    main()
