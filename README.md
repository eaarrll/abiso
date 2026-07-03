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

## Data sources

- PAGASA CAP 1.2 Atom feed (CC BY 4.0) — official alerting backbone
- UP NOAH hazard maps (ODbL) — flood / storm surge / landslide susceptibility per barangay
- PSA PSGC (quarterly) + OCHA COD-AB ADM4 — barangay codes and boundaries
