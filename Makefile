PY := .venv/bin/python

.PHONY: all setup dataprep psgc boundaries noah hazard poll track test clean

all: dataprep

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -e ".[dev]"

# --- Phase 0: data prep -------------------------------------------------
# psgc:       gazetteer from psgc.gitlab.io API (set PSGC_XLSX=path to the
#             PSA publication file to enrich with correspondence codes)
# boundaries: OCHA COD-AB ADM4 barangay polygons, filtered to REGION (07)
# noah:       NOAH hazard shapefiles from the bettergovph mirror
#             (NOAH_PROVINCES="Cebu" by default; comma-separated)
# hazard:     NOAH x ADM4 intersection -> warehouse/barangay_hazard.parquet
dataprep: psgc boundaries noah hazard

psgc:
	$(PY) -m pipeline.dataprep.psgc

boundaries:
	$(PY) -m pipeline.dataprep.boundaries

noah:
	$(PY) -m pipeline.dataprep.noah

hazard:
	$(PY) -m pipeline.dataprep.hazard_intersect

# --- Phase 1: ingest (one-shot; cron/scheduler in deploy) ----------------
poll:
	$(PY) -m pipeline.ingest.cap_poller

track:
	$(PY) -m pipeline.ingest.track

# --- Phase 4: deliver -----------------------------------------------------
# dispatch: send everything fetched-but-undispatched (dry-run unless
#           ABISO_LIVE=1 and channel credentials are set)
# serve:    inbound webhook (sms/telegram/messenger replies)
dispatch:
	$(PY) -m pipeline.deliver.dispatch --new

serve:
	.venv/bin/uvicorn pipeline.deliver.webhook:app --host 0.0.0.0 --port 8080

test:
	$(PY) -m pytest -q

clean:
	rm -rf warehouse/*.parquet data/cap data/track
