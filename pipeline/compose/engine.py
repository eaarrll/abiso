"""Compose: select a template for a CAP alert and slot-fill it.

Safety properties enforced here, per PLAN.md §2 (no exceptions in the
alert path):
- fail-closed — a missing slot or unmatched alert raises ComposeError;
  nothing partial ever renders
- draft gate — languages whose text is not human-approved
  (status: approved) refuse to render unless allow_draft is passed,
  which only tests and preview tooling may do
- SMS constraints — GSM-7 septet counting (brackets etc. cost 2),
  honest segment counts, hard rejection of URLs (NTC blocks them)
- every message carries PAGASA attribution + the bulletin timestamp
- templates carry no numbers or claims not present in the bulletin
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from pipeline import config
from pipeline.ingest.cap import CapAlert

PH_TZ = ZoneInfo("Asia/Manila")

_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTHS_FIL = ["Ene", "Peb", "Mar", "Abr", "May", "Hun",
               "Hul", "Ago", "Set", "Okt", "Nob", "Dis"]
MONTHS = {"en": _MONTHS_EN, "fil": _MONTHS_FIL, "ceb": _MONTHS_FIL}

# Acknowledgment request, appended for the BDRRMC tier only (ack loop).
# "Reply" is standard PH texting vocabulary across languages.
ACK = {
    "en": "Reply YES if received.",
    "fil": "Reply OO kung natanggap.",
    "ceb": "Reply OO kung nadawat.",
}

# GSM 03.38 basic charset (1 septet) and extension table (2 septets).
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXT = set("^{}\\[~]|€")


class ComposeError(Exception):
    """Raised whenever a message cannot be rendered safely. Never catch
    this to 'send anyway' — it means a human needs to look."""


def gsm_septets(text: str) -> int | None:
    """Septet count under GSM-7, or None if the text needs UCS-2."""
    n = 0
    for ch in text:
        if ch in _GSM_BASIC:
            n += 1
        elif ch in _GSM_EXT:
            n += 2
        else:
            return None
    return n


def sms_segments(text: str) -> int:
    septets = gsm_septets(text)
    if septets is None:  # UCS-2: 70 chars single, 67 per concat segment
        return 1 if len(text) <= 70 else math.ceil(len(text) / 67)
    return 1 if septets <= 160 else math.ceil(septets / 153)


@dataclass
class Rendered:
    template_id: str
    language: str
    channel: str
    tier: str
    text: str
    segments: int
    warnings: list[str] = field(default_factory=list)


class TemplateBook:
    def __init__(self, templates_dir: Path | None = None):
        d = templates_dir or (config.ROOT / "templates")
        self.templates: list[dict] = []
        for f in sorted(d.glob("*.yaml")):
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            for t in doc.get("templates", []):
                t.setdefault("priority", 0)
                t["_file"] = f.name
                self.templates.append(t)
        if not self.templates:
            raise ComposeError(f"no templates found under {d}")
        self.templates.sort(key=lambda t: -t["priority"])

    # --- selection -----------------------------------------------------

    def select(self, alert: CapAlert) -> dict:
        if not alert.infos:
            raise ComposeError(f"alert {alert.identifier} has no <info>")
        info = alert.infos[0]
        for t in self.templates:
            if self._matches(t.get("match", {}), alert, info):
                return t
        raise ComposeError(
            f"no template matches event={info.event!r} "
            f"severity={info.severity!r} msg_type={alert.msg_type!r} — "
            "a human must review this alert"
        )

    @staticmethod
    def _matches(m: dict, alert: CapAlert, info) -> bool:
        if "msg_type" in m and alert.msg_type != m["msg_type"]:
            return False
        if "event_rx" in m and not re.search(m["event_rx"], info.event, re.I):
            return False
        if "severity" in m:
            wanted = m["severity"] if isinstance(m["severity"], list) else [m["severity"]]
            if info.severity not in wanted:
                return False
        if "param" in m:
            name_rx = re.compile(m["param"]["name_rx"], re.I)
            value_rx = re.compile(m["param"]["value_rx"], re.I)
            if not any(
                name_rx.search(n) and value_rx.search(v)
                for n, v in info.parameters
            ):
                return False
        return True

    # --- rendering -------------------------------------------------------

    def render(
        self,
        alert: CapAlert,
        template: dict,
        language: str,
        channel: str = "sms",
        slots: dict | None = None,
        tier: str = "bdrrmc",
        allow_draft: bool = False,
    ) -> Rendered:
        lang_block = template.get("text", {}).get(language)
        if lang_block is None:
            raise ComposeError(f"template {template['id']} has no {language!r} text")
        status = lang_block.get("status", "needs_review")
        if status != "approved" and not allow_draft:
            raise ComposeError(
                f"template {template['id']}/{language} is {status!r} — "
                "human validation required before this language can be sent"
            )
        body = lang_block.get(channel) or lang_block.get("sms")
        if not body:
            raise ComposeError(f"template {template['id']}/{language} has no text body")

        stamp = self._stamp(alert, language)
        values = {
            "attrib": f"PAGASA {template['label']} {stamp}",
            "stamp": stamp,
            "ack": ACK.get(language, ACK["en"]) if tier == "bdrrmc" else "",
            **(slots or {}),
        }
        try:
            text = body.format(**values)
        except KeyError as e:
            raise ComposeError(
                f"missing slot {e} for template {template['id']}/{language} — "
                "refusing to send incomplete instructions"
            ) from e

        if channel == "sms":
            text = re.sub(r"\s+", " ", text).strip()
        else:  # rich channels keep their line structure
            text = "\n".join(
                re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()
            ).strip()
        if re.search(r"https?://|www\.", text, re.I):
            raise ComposeError("URL in alert text — PH carriers block SMS with links")

        warnings = []
        segments = 1
        if channel == "sms":
            if gsm_septets(text) is None:
                warnings.append("non-GSM characters: will be sent as UCS-2")
            segments = sms_segments(text)
            if segments > 1:
                warnings.append(f"{segments} SMS segments")
        if status != "approved":
            warnings.append(f"DRAFT ({status}) — not for live sending")

        return Rendered(
            template_id=template["id"],
            language=language,
            channel=channel,
            tier=tier,
            text=text,
            segments=segments,
            warnings=warnings,
        )

    @staticmethod
    def _stamp(alert: CapAlert, language: str) -> str:
        iso = (alert.infos[0].effective if alert.infos else "") or alert.sent
        dt = datetime.fromisoformat(iso).astimezone(PH_TZ)
        mon = MONTHS.get(language, _MONTHS_EN)[dt.month - 1]
        clock = dt.strftime("%I:%M%p").lstrip("0").replace(":00", "")
        return f"{mon}{dt.day} {clock}"
