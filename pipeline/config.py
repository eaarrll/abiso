"""Central paths and endpoints. Everything overridable via environment."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CAP_DIR = DATA / "cap"
TRACK_DIR = DATA / "track"
WAREHOUSE = ROOT / "warehouse"
REFERENCE = ROOT / "reference"

# PAGASA CAP feed: official, CC BY 4.0. The only open machine-readable
# alert source — see PLAN.md §2.
CAP_FEED_URL = os.environ.get(
    "CAP_FEED_URL", "https://publicalert.pagasa.dost.gov.ph/feeds/"
)
TYPHOON_TRACK_URL = os.environ.get(
    "TYPHOON_TRACK_URL",
    "https://webgis-static.up.edu.ph/api/typhoon_track/pagasa_typhoon.geojson",
)

PSGC_API_BASE = os.environ.get("PSGC_API_BASE", "https://psgc.gitlab.io/api")
# Optional: path to the PSA quarterly publication XLSX (authoritative;
# carries the old-code crosswalk the API lacks). PSA blocks scripted
# downloads, so this is a manual drop-in.
PSGC_XLSX = os.environ.get("PSGC_XLSX", "")

HDX_PACKAGE_URL = os.environ.get(
    "HDX_PACKAGE_URL",
    "https://data.humdata.org/api/3/action/package_show?id=cod-ab-phl",
)

NOAH_HF_REPO = os.environ.get("NOAH_HF_REPO", "bettergovph/project-noah-hazard-maps")
NOAH_PROVINCES = [
    p.strip()
    for p in os.environ.get("NOAH_PROVINCES", "Cebu").split(",")
    if p.strip()
]

# Pilot scope: Region VII (Cebu incl. HUCs). 10-digit PSGC region prefix.
REGION = os.environ.get("REGION", "07")

GAZETTEER_PARQUET = WAREHOUSE / "gazetteer.parquet"
ADM4_PARQUET = WAREHOUSE / f"adm4_region{REGION}.parquet"
BARANGAY_HAZARD_PARQUET = WAREHOUSE / "barangay_hazard.parquet"

USER_AGENT = "abiso-relay/0.1 (barangay disaster alert relay; non-commercial)"
