# Barangay Alert Relay — Implementation Plan

**Working name:** Abiso (Cebuano/Filipino: "notice/warning")
**Confirmed:** 2026-07-03 — architecture as gated; pilot = **Cebu province**; v1 = **relay + acknowledgment loop**.

## 1. What this is

A last-mile relay that turns official PAGASA warnings into barangay-specific, local-language,
plain-instruction alerts, delivered over redundant channels (SMS + Viber + Telegram), with
acknowledgment tracking on barangay DRRM contacts and escalation on silence.

**The gap it fills** (OECD Dev Policy Paper No. 59, Nov 2025; 2024–25 typhoon post-mortems):
warnings exist and are accurate, but die between the provincial bulletin and the household —
only ~34% of LGUs have operational EWS, rainfall warnings are systematically misread
(people calibrate to wind-signal numbers: Kristine 2024, Kalmaegi/Tino 2025), and every
existing channel is fire-and-forget. RA 12287 (2025 anticipatory-action law) now obliges
LGUs to act on forecasts — demand tailwind.

**What it is NOT:** a forecaster or an evacuation authority. It relays and translates official
warnings only, always attributed to PAGASA and timestamped. Evacuation orders belong to the
punong barangay / LDRRMC (RA 10121). Altering or inventing warnings risks RPC Art. 154.

## 2. Architecture (confirmed at gate)

```
PAGASA CAP feed ──poll──▶ INGEST ──▶ DOWNSCALE ──▶ COMPOSE ──▶ DELIVER ──▶ ACK LOOP
(publicalert.pagasa       parse CAP   province →     slot-fill    SMS         reply-YES
 .dost.gov.ph/feeds/)     1.2 XML     barangay       templates    Viber       tracking +
NOAH typhoon track                    fan-out via    (Cebuano +   Telegram    escalation
(webgis-static GeoJSON)               barangay_      Filipino)    (Messenger
                                      hazard table                inbound only)
```

### Ingest
- Poll the PAGASA CAP Atom feed every 5 min (`https://publicalert.pagasa.dost.gov.ph/feeds/`).
  Official, CC BY 4.0, XML-DSig signed, PubSubHubbub hub available (Google alert-hub) —
  start with polling, add hub subscription later.
- Entry types: `gfa` (General Flood Advisory), `wcp` (TC wind signals), `acp`/`tcp` (TC alerts/
  bulletins). Parse CAP 1.2: event, severity, urgency, `msgType` (Alert/Update/Cancel —
  handle supersession), effective/expires, SAME geocodes (9-digit PSGC province codes),
  polygons.
- Also poll `https://webgis-static.up.edu.ph/api/typhoon_track/pagasa_typhoon.geojson`
  (live track + forecast positions) for context lines ("landfall expected ~midnight").
- Idempotency: CAP `identifier` is the natural key; store raw XML for audit.
- Gap accepted for v1: municipality-level wind-signal enumeration lives only in TCB PDFs
  (unofficial `pagasa-parser` exists) — v1 fans out at province level refined by hazard
  susceptibility; revisit in v2.

### Downscale (the value-add)
- **Static prep (offline, one-time + refreshes):**
  - PSA PSGC quarterly XLSX → gazetteer (10-digit codes, names, alias table, old-code
    crosswalk). Re-ingest every quarter, diff, alert on renames. **Never match barangays by
    name — PSGC codes only.**
  - OCHA COD-AB PHL ADM4 barangay polygons (P-coded, PSGC-2023 vintage) into PostGIS;
    reconciliation table for post-2023 PSGC churn.
  - NOAH hazard shapefiles (flood 5/25/100-yr, storm surge SSA1–4, landslide; ODbL via
    `bettergovph/project-noah-hazard-maps` mirror — pull **Region VII only** for the pilot)
    ∩ ADM4 → `barangay_hazard(psgc, hazard_type, severity_class, pct_area, est_pop)`.
- **At alert time:** CAP geocode/polygon → subscribed barangays in scope (any-intersect,
  over-inclusive by design) → rank/annotate by `barangay_hazard` for the CAP event type →
  per-barangay alert instance.

### Compose
- Parameterized template matrix: hazard × severity × action. v1 ≈ 15–20 templates covering
  rainfall warnings (yellow/orange/red), GFA levels, TC wind signals 1–5, storm surge
  advisories. **Rainfall templates are the priority** — that's the killer hazard.
- Languages for pilot: **Cebuano + Filipino** (dual-issue always). Human-translated once,
  community-validated with pilot BDRRMCs, using PAGASA/KWF controlled vocabulary
  (e.g., *daluyong* = storm surge). Slots pass through untranslated: barangay name,
  evacuation center, times, river names.
- **No machine translation and no free-form LLM generation in the alert path** (FilBench
  2025: hallucination risk in PH languages). LLMs allowed only for non-alert content with
  human review.
- Every message: `[PAGASA <bulletin type>, <timestamp>]` attribution; imperative advisory
  phrasing, never "order"; SMS variant ≤160 chars, **zero URLs** (NTC MC 12-12-2023 —
  carriers block them).
- Example (Cebuano, red rainfall):
  `ABISO [PAGASA Red Rainfall, Hul 3 8PM]: Brgy <X>, Cebu City — kusog nga ulan sulod sa
  3 oras. Ang sapa dinhi kasagarang mo-awas. Bakwit na sa <evac center> karon. Reply OO
  kung nadawat.`

### Deliver
- **SMS (backbone):** local aggregator — Semaphore (₱0.56/seg, simple API) to start; evaluate
  iTexMo (₱0.15–0.50) at volume. Alphanumeric sender ID ("ABISO" or org name, ≤11 chars) —
  **registration takes 2–4 weeks; file on day 1.** Documented opt-in per Data Privacy Act.
- **Viber Community (free rich layer):** one Community per barangay, 71% PH penetration;
  bot API skipped (€100/mo). v1: semi-manual posting by the on-call runbook or a helper
  that produces copy-paste-ready posts; admin device automation later.
- **Telegram channel:** free mirror via Bot API; near-zero effort.
- **Messenger:** Page + webhook bot, **inbound only** — "status sa akong barangay?" queries
  and within-24h follow-ups. No push (all broadcast paths deprecated by Apr 2026).
- Two recipient tiers per barangay:
  1. **BDRRMC tier** (captain, kagawad on DRRM, tanod lead): SMS always, all severities,
     in the ack loop.
  2. **Resident tier** (opt-in list): SMS on high severity only (cost control) + Viber/
     Telegram for everything.

### Ack loop (the differentiator)
- Every BDRRMC alert asks for a reply (`OO`/`YES` — inbound SMS via aggregator webhook or
  virtual number; Viber/Telegram replies also count).
- Escalation ladder on silence: 10 min → re-send SMS to all BDRRMC contacts; 25 min → 
  voice-call task or alternate contact; 40 min → notify municipal DRRMO contact. Timings
  configurable per severity.
- Every send/deliver/ack/escalate event logged → per-event timeline: *which barangays
  acknowledged, how fast, who never did*. This audit trail is the artifact LGUs need under
  RA 12287, and the pilot's proof of value.

## 3. Stack (boring, one-person deployable)

- Python 3.12, FastAPI (webhooks: aggregator inbound SMS, Telegram, Messenger), APScheduler
  for polling, `httpx`, `lxml` for CAP.
- Postgres + PostGIS (single instance; also the queue — no broker in v1).
- Geo prep as offline scripts/Makefile targets (GeoPandas/ogr2ogr) writing lookup tables;
  runtime does table joins, not GIS.
- Deploy: single small VM (Fly.io/Hetzner/Lightsail Singapore region), Docker Compose,
  daily pg_dump offsite. Secrets in env.
- Repo layout mirrors `flood-watchdog`: `pipeline/` (ingest/downscale/compose/deliver/ack),
  `dataprep/`, `templates/` (per-language YAML), `warehouse/` (schemas), `Makefile`, `PLAN.md`.

## 4. Cebu pilot

- **Why Cebu (confirmed):** Kalmaegi/Tino (Nov 2025) was the deadliest recent dissemination
  failure — worst flash flood in Cebu's history, ~241 dead/missing, residents asleep because
  timing shifted and rainfall ≠ wind-signal mental model; all 27 flooded Cebu City barangays
  had NOAH flood maps. Cebuano templates later unlock much of Visayas/Mindanao.
- **Scope:** 3–5 barangays, mixing one urban flash-flood barangay (Cebu City riverside —
  e.g., along Butuanon or Mahiga creek areas flagged in `barangay_hazard`) and coastal
  storm-surge barangays (north Cebu). Note: Komunidad has a funded MHEWS pilot in **Mandaue**
  — avoid Mandaue or approach as complement, not competitor.
- **Recruitment path:** city/municipal DRRMO endorsement first (they own the barangay
  relationship), Red Cross 143 chapter and Cebu civic-tech community as fallback intros.
- **Evacuation centers:** no national dataset exists — collect per pilot barangay during
  onboarding (it's 1–3 designated sites each; usually the school or covered court).
- **Success criteria for the season:** ≥95% BDRRMC ack rate within 15 min on drills; at least
  one live event where the barangay confirms the alert arrived before/alongside official
  channels with clearer instructions; zero incidents of the relay being mistaken for an
  original forecast.

## 5. Phases

| # | Phase | Contents | Effort |
|---|-------|----------|--------|
| 0 | Foundations (start now) | Sender-ID registration filed (2–4 wk lead); PSGC + COD-AB + NOAH Region VII acquired; gazetteer + `barangay_hazard` built; Postgres schema | ~1 wk active |
| 1 | Ingest | CAP poller/parser, Update/Cancel handling, typhoon-track poller, raw-XML audit store; replay tests against archived TD Henry CAP files | ~1 wk |
| 2 | Downscale + subscribe | Fan-out logic, subscription model (barangay, tier, contact, consent record), admin CLI | ~1 wk |
| 3 | Compose | Template YAML schema, 15–20 Cebuano+Filipino templates drafted → professional translation → BDRRMC validation session | 1–2 wk (translation is the long pole) |
| 4 | Deliver | Semaphore integration + inbound webhook, Telegram bot, Viber Community runbook/copy-helper, Messenger inbound bot | ~1 wk |
| 5 | Ack loop | Reply matching, escalation scheduler, event timeline report (start as a generated Markdown/HTML report, not a dashboard) | ~1 wk |
| 6 | Pilot ops | Onboard 3–5 barangays, 2 drills, live-shadow one storm, iterate templates | rest of season |

Phase 0–1 can run while sender-ID registration is pending; Telegram/Viber work without it.

## 6. Costs

- SMS: ~₱1,200–4,500/mo per ~2,000-household barangay at 4 alerts/mo (resident tier on
  high-severity-only keeps pilot closer to the low end; BDRRMC tier is ~10 contacts ≈ pennies).
- Everything else ₱0 (Viber Community, Telegram, Messenger inbound, CAP/NOAH data).
- Infra: ~US$10–20/mo VM + backups.
- One-time: professional translation of the template matrix (Cebuano pilot set: small;
  budget grows with languages), sender-ID registration fee via aggregator.

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| CAP feed outage mid-event | Fallback scraper for bagong.pagasa TCB page; alert operator on feed silence > 6h during an active PAR system |
| Being mistaken for PAGASA / false-news exposure | Fixed attribution+timestamp in every template; "advisory not order" phrasing reviewed by counsel; never publish original predictions |
| SMS aggregator failure during typhoon | Second aggregator account (iTexMo) pre-registered as warm standby |
| Towers down (the OECD critique) | Redundancy is the design; ack-loop escalation surfaces unreachable barangays to the municipal DRRMO while backhaul may still exist |
| PSGC/boundary churn (esp. BARMM later) | Quarterly PSGC diff job; reconciliation table; over-inclusive fan-out |
| Template wrong in a local language | Community validation before go-live; slots never translated; dual-issue with Filipino |
| Volunteer-project death (eBayanihan pattern) | Keep run cost ≤ ₱5k/mo/barangay; design for LGU handover — LDRRM fund's 70% preparedness tranche + RA 12287 give LGUs a legal funding line to adopt it |

## 8. Deferred (v2+)

- Municipality-level wind signals from TCB PDFs (pagasa-parser or own parser).
- DOST-ASTI PhilSensors API access request (rain/water-level ground truth; NOAH's own IoT
  network is partially stale) — file the request early, it's free to ask.
- PubSubHubbub push subscription instead of polling.
- More languages (Bikol, Waray next — highest exposure), more regions.
- LGU-facing web dashboard (reports-as-files until someone asks).
- Viber admin-device automation for Community posting.

## 9. Key sources (verified July 2026)

- PAGASA CAP: `https://publicalert.pagasa.dost.gov.ph/feeds/` (CC BY 4.0)
- NOAH track: `https://webgis-static.up.edu.ph/api/typhoon_track/pagasa_typhoon.geojson`
- NOAH hazard mirror: `https://huggingface.co/datasets/bettergovph/project-noah-hazard-maps` (ODbL)
- PSGC: `https://psa.gov.ph/classification/psgc` (quarterly XLSX; API mirror `https://psgc.gitlab.io/api/`)
- Boundaries: `https://data.humdata.org/dataset/cod-ab-phl` (ADM4)
- OECD paper No. 59 (Nov 2025): last-mile/EWS gap analysis
- Semaphore: `https://semaphore.co` (₱0.56/SMS); iTexMo: `https://itexmo.com`
- Legal: RA 10121, RA 10692, RA 10639, RA 12287; IP Code §176; RPC Art. 154
