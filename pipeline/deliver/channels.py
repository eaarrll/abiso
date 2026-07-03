"""Channel adapters. Safety default: DRY RUN.

Nothing leaves the machine unless ABISO_LIVE=1 AND the channel has
credentials — otherwise the send is printed and recorded with
dry_run: true, so the whole pipeline is rehearsable end-to-end
(drills, tests, template review) with zero risk of paging a barangay.
"""

from dataclasses import dataclass
from pathlib import Path

import requests

from pipeline import config


@dataclass
class SendResult:
    ok: bool
    channel: str
    kind: str  # queued (dry-run) | sent | failed
    detail: dict


def _dry(channel: str, **detail) -> SendResult:
    return SendResult(True, channel, "queued", {"dry_run": True, **detail})


def send_sms(msisdn: str, text: str) -> SendResult:
    """Semaphore (semaphore.co) — PH-local aggregator, ₱0.56/segment."""
    if not (config.LIVE and config.SEMAPHORE_API_KEY):
        print(f"  [dry-run sms -> {msisdn}] {text}")
        return _dry("sms", msisdn=msisdn, text=text)
    try:
        r = requests.post(
            config.SEMAPHORE_URL,
            data={
                "apikey": config.SEMAPHORE_API_KEY,
                "number": msisdn,
                "message": text,
                "sendername": config.SMS_SENDER_NAME,
            },
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        first = body[0] if isinstance(body, list) and body else body
        return SendResult(True, "sms", "sent",
                          {"provider_id": first.get("message_id"), "msisdn": msisdn})
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        return SendResult(False, "sms", "failed", {"error": str(e), "msisdn": msisdn})


def post_telegram(chat_id: str, text: str) -> SendResult:
    if not (config.LIVE and config.TELEGRAM_BOT_TOKEN):
        print(f"  [dry-run telegram -> {chat_id}] {text[:80]}…")
        return _dry("telegram", chat_id=chat_id, text=text)
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        r.raise_for_status()
        return SendResult(True, "telegram", "sent",
                          {"message_id": r.json().get("result", {}).get("message_id")})
    except (requests.RequestException, ValueError) as e:
        return SendResult(False, "telegram", "failed", {"error": str(e)})


def viber_outbox(alert_identifier: str, psgc: str, text: str,
                 outbox: Path | None = None) -> SendResult:
    """Viber Communities are posted manually (bot API costs €100/mo —
    PLAN.md §2). Write copy-paste-ready text to the outbox; the on-call
    runbook is: open file, paste into the barangay Community, done."""
    base = outbox or (config.OUTBOX / "viber")
    base.mkdir(parents=True, exist_ok=True)
    safe_id = alert_identifier.replace("/", "_")
    path = base / f"{safe_id}_{psgc}.txt"
    path.write_text(text, encoding="utf-8")
    return SendResult(True, "viber", "queued", {"manual": True, "file": str(path)})
