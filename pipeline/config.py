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

# --- Delivery (Phase 4) --------------------------------------------------
# SQLite for the pilot (single VM, zero ops); warehouse/schema.sql is the
# Postgres target when scale demands it.
DB_PATH = Path(os.environ.get("ABISO_DB", str(DATA / "abiso.db")))
OUTBOX = DATA / "outbox"

# Nothing leaves the machine unless ABISO_LIVE=1 AND the channel has
# credentials. Default is dry-run: sends are logged + recorded, not made.
LIVE = os.environ.get("ABISO_LIVE") == "1"

SEMAPHORE_API_KEY = os.environ.get("SEMAPHORE_API_KEY", "")
SEMAPHORE_URL = os.environ.get("SEMAPHORE_URL", "https://api.semaphore.co/api/v4/messages")
SMS_SENDER_NAME = os.environ.get("SMS_SENDER_NAME", "ABISO")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MESSENGER_VERIFY_TOKEN = os.environ.get("MESSENGER_VERIFY_TOKEN", "")

# Inbound webhook authentication. A reply of "OO" stops an escalation, so an
# unauthenticated endpoint would let anyone silence the ladder. Fail closed:
# a channel whose secret is unset answers 503 and records nothing.
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "")  # SMS aggregator callback
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")  # setWebhook secret_token
MESSENGER_APP_SECRET = os.environ.get("MESSENGER_APP_SECRET", "")  # signs X-Hub-Signature-256

# Resident tier gets SMS only on high severity (cost control; PLAN.md §2).
# BDRRMC tier always gets SMS.
RESIDENT_SMS_SEVERITIES = {"Extreme", "Severe"}

# --- Ack loop (Phase 5) ---------------------------------------------------
# Replies counted as acknowledgment, normalized to bare uppercase tokens.
ACK_WORDS = {"OO", "YES", "OK", "OPO", "SIGE", "NADAWAT", "NATANGGAP", "RECEIVED"}
# A reply only acks alerts sent within this window.
ACK_WINDOW_HOURS = 24
# Escalation ladder on BDRRMC silence, minutes after the first send
# (PLAN.md §2: resend -> voice-call task/alternate -> municipal DRRMO).
# At most one step fires per run; run every ~5 min from cron.
ESCALATION_LADDER = [
    (10, "resend_sms"),
    (25, "notify_alternate"),
    (40, "notify_drrmo"),
]
# Instances older than this are no longer escalated (the event has passed;
# the timeline report is the record).
ESCALATION_WINDOW_HOURS = 6

REPORTS = DATA / "reports"
