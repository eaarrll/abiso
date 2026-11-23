"""Snapshot the NOAH/PAGASA live typhoon track GeoJSON.

Content-addressed by hash so repeated polls of an unchanged track write
nothing; each distinct forecast state is kept for the event timeline.
"""

import hashlib
import json
from datetime import UTC, datetime

import requests

from pipeline import config


def poll() -> None:
    config.TRACK_DIR.mkdir(parents=True, exist_ok=True)
    r = requests.get(
        config.TYPHOON_TRACK_URL, headers={"User-Agent": config.USER_AGENT}, timeout=60
    )
    r.raise_for_status()
    digest = hashlib.sha256(r.content).hexdigest()[:12]
    existing = list(config.TRACK_DIR.glob(f"*_{digest}.geojson"))
    if existing:
        print(f"track: unchanged ({existing[0].name})")
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = config.TRACK_DIR / f"{stamp}_{digest}.geojson"
    dest.write_bytes(r.content)

    try:
        gj = json.loads(r.content)
        feats = gj.get("features", [])
        names = {
            f["properties"].get("name") or f["properties"].get("NAME")
            for f in feats
            if isinstance(f.get("properties"), dict)
        } - {None}
        print(f"track: saved {dest.name} ({len(feats)} features; storms: {sorted(names) or 'n/a'})")
    except (json.JSONDecodeError, KeyError):
        print(f"track: saved {dest.name} (could not summarize)")


if __name__ == "__main__":
    poll()
