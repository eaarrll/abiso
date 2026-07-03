# Abiso — Barangay Alert Relay

Last-mile disaster alert relay for Philippine barangays. Subscribes a barangay once, then
relays official PAGASA warnings as barangay-specific, Cebuano/Filipino plain-language
instructions over SMS, Viber, and Telegram — with acknowledgment tracking on barangay DRRM
contacts and escalation on silence.

Relay only: every message is attributed to PAGASA and timestamped. This service never
forecasts and never orders evacuation — that authority belongs to the punong barangay and
the local DRRM council (RA 10121).

**Status:** planning complete (see [PLAN.md](PLAN.md)); Cebu province pilot, 2026 typhoon season.

## Pipeline

```
PAGASA CAP feed → ingest → downscale (province → barangay via PSGC + NOAH hazard
susceptibility) → compose (human-translated templates, slot-filled) → deliver
(SMS / Viber / Telegram) → ack loop (reply-YES, escalation, event timeline)
```

## Quickstart

```sh
make setup       # venv + deps
make dataprep    # psgc -> boundaries -> noah -> hazard (Phase 0 artifacts)
make poll        # one-shot PAGASA CAP feed poll (cron every 5 min in deploy)
make track       # snapshot the live typhoon-track GeoJSON
make test
```

Fan out a fetched alert to barangays:

```sh
.venv/bin/python -m pipeline.downscale.fanout data/cap/raw/<alert>.xml
```

Preview composed messages (Phase 3; `--allow-draft` renders languages that
are still awaiting human validation):

```sh
.venv/bin/python -m pipeline.compose.render tests/fixtures/gfa_sample.cap \
    --psgc 0702219002 --allow-draft
```

Templates live in `templates/*.yaml` (see `templates/README.md` for the
translation workflow — Filipino/Cebuano drafts must be human-approved
before the engine will render them for sending).

Deliver (Phase 4 — **dry-run by default**; nothing is sent unless
`ABISO_LIVE=1` and channel credentials are set):

```sh
.venv/bin/python -m pipeline.deliver.subscribers add \
    --psgc 0702219002 --tier bdrrmc --name "Kap. Juan Dela Cruz" \
    --msisdn +639171234567 --consent-ref "onboarding form 2026-07-10"
make dispatch    # send everything fetched-but-undispatched
make serve       # inbound webhook (sms/telegram/messenger replies)
```

Live-send env: `ABISO_LIVE=1`, `SEMAPHORE_API_KEY`, `SMS_SENDER_NAME`
(registered sender ID), `TELEGRAM_BOT_TOKEN`, `MESSENGER_VERIFY_TOKEN`.
Viber Communities are posted manually from `data/outbox/viber/` (the bot
API's €100/month fee is not worth it at barangay scale). Every send,
failure, skip, and reply lands in SQLite (`data/abiso.db`) — the
`delivery_event` table is the RA 12287 audit timeline.

Environment knobs (see `pipeline/config.py`): `NOAH_PROVINCES` (default
`Cebu`), `REGION` (default `07`), `PSGC_XLSX` (path to the PSA quarterly
publication file, refreshed every quarter).

## Data sources

- PAGASA CAP 1.2 Atom feed (CC BY 4.0) — official alerting backbone
- UP NOAH hazard maps (ODbL) — flood / storm surge / landslide susceptibility per barangay
- PSA PSGC (quarterly) + OCHA COD-AB ADM4 — barangay codes and boundaries
