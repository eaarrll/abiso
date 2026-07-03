"""Preview CLI: run a CAP alert through select -> fan-out -> slot-fill.

For humans checking templates and translators reviewing drafts — hence
--allow-draft exists here. The deliver phase (Phase 4) calls the engine
directly and can NOT pass allow_draft.

usage:
  python -m pipeline.compose.render <alert.xml> [--psgc CODE]
         [--lang ceb,fil,en] [--channel sms] [--tier bdrrmc] [--allow-draft]
"""

import argparse

import yaml

from pipeline import config
from pipeline.compose.engine import ComposeError, TemplateBook
from pipeline.downscale.fanout import Downscaler
from pipeline.ingest.cap import parse_cap


def _load_profiles() -> dict:
    path = config.REFERENCE / "pilot_barangays.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("alert_xml")
    ap.add_argument("--psgc", help="render for this barangay (default: top fan-out hit)")
    ap.add_argument("--lang", default="en,fil,ceb")
    ap.add_argument("--channel", default="sms")
    ap.add_argument("--tier", default="bdrrmc", choices=["bdrrmc", "resident"])
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()

    alert = parse_cap(open(args.alert_xml, "rb").read())
    book = TemplateBook()
    template = book.select(alert)
    print(f"template: {template['id']} (from {template['_file']})")

    result = Downscaler().fan_out(alert)
    if not result.hits:
        raise SystemExit("fan-out produced no barangays")
    hit = next((h for h in result.hits if h.psgc == args.psgc), result.hits[0])
    print(f"barangay: {hit.psgc} {hit.name} (via {hit.matched_via}, "
          f"hazard pct_area={hit.max_pct_area:.2f})\n")

    profile = _load_profiles().get(hit.psgc, {})
    slots = {"bgy": profile.get("short_name") or f"Brgy {hit.name}"}
    if profile.get("evac_center"):
        slots["evac"] = profile["evac_center"]

    for lang in args.lang.split(","):
        try:
            r = book.render(
                alert, template, lang.strip(), channel=args.channel,
                slots=slots, tier=args.tier, allow_draft=args.allow_draft,
            )
        except ComposeError as e:
            print(f"[{lang}] REFUSED: {e}\n")
            continue
        flags = f"  ({'; '.join(r.warnings)})" if r.warnings else ""
        print(f"[{lang}] {r.segments} segment(s){flags}\n{r.text}\n")


if __name__ == "__main__":
    main()
