"""One-shot CAP feed poll: fetch new entries, archive raw XML, emit
normalized JSON lines.

Raw XML is kept verbatim under data/cap/raw/ — it is the audit trail
(signed by PAGASA) that everything downstream must be traceable to.
Filesystem presence doubles as seen-state, so the poller is idempotent
and safe to run from cron every 5 minutes.
"""

import dataclasses
import json
import re
from urllib.parse import urljoin

import requests

from pipeline import config
from pipeline.ingest.cap import parse_atom, parse_cap

RAW_DIR = config.CAP_DIR / "raw"
ALERTS_JSONL = config.CAP_DIR / "alerts.jsonl"


def _safe_name(url: str) -> str:
    # .../output/gfa/{uuid}.cap -> gfa_{uuid}.xml
    m = re.search(r"output/(\w+)/([\w-]+)\.cap", url)
    if m:
        return f"{m.group(1)}_{m.group(2)}.xml"
    return re.sub(r"[^\w.-]", "_", url.rsplit("/", 2)[-1]) + ".xml"


def poll(feed_url: str = config.CAP_FEED_URL) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": config.USER_AGENT}

    r = requests.get(feed_url, headers=headers, timeout=60)
    r.raise_for_status()
    entries = parse_atom(r.content)
    print(f"poll: feed has {len(entries)} entries")

    new = 0
    with open(ALERTS_JSONL, "a", encoding="utf-8") as out:
        for entry in entries:
            if not entry.cap_url:
                continue
            cap_url = urljoin(feed_url, entry.cap_url)
            dest = RAW_DIR / _safe_name(cap_url)
            if dest.exists():
                continue
            cr = requests.get(cap_url, headers=headers, timeout=60)
            if cr.status_code != 200:
                print(f"poll: {cr.status_code} on {cap_url} — will retry next run")
                continue
            alert = parse_cap(cr.content)
            dest.write_bytes(cr.content)
            record = dataclasses.asdict(alert)
            record["_cap_url"] = cap_url
            record["_raw_file"] = dest.name
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            new += 1
            sev = alert.infos[0].severity if alert.infos else "?"
            event = alert.infos[0].event if alert.infos else "?"
            print(f"poll: NEW [{alert.msg_type}/{sev}] {event} ({dest.name})")

    print(f"poll: {new} new alerts")
    return new


if __name__ == "__main__":
    poll()
