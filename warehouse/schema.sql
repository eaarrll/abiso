-- Runtime schema (Postgres + PostGIS) for the deployed relay.
-- Phase 0-2 run file-based (parquet under warehouse/); this schema is the
-- target the deliver + ack phases load into. Postgres doubles as the queue.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Gazetteer (from warehouse/gazetteer.parquet, refreshed quarterly)
CREATE TABLE IF NOT EXISTS gazetteer (
    psgc          char(10) PRIMARY KEY,
    name          text NOT NULL,
    level         text NOT NULL,           -- Reg | Prov | City/Mun | Bgy
    region_code   char(2) NOT NULL,
    province_code char(5) NOT NULL,
    citymun_code  char(7) NOT NULL,
    old_code      char(9)                  -- PSA correspondence code (SAME geocode key)
);
CREATE INDEX IF NOT EXISTS gazetteer_old_code ON gazetteer (old_code);
CREATE INDEX IF NOT EXISTS gazetteer_province ON gazetteer (province_code) WHERE level = 'Bgy';

-- Precomputed NOAH x ADM4 susceptibility (from barangay_hazard.parquet)
CREATE TABLE IF NOT EXISTS barangay_hazard (
    psgc        char(10) NOT NULL REFERENCES gazetteer,
    hazard_type text NOT NULL,             -- flood | storm_surge | landslide
    scenario    text NOT NULL,             -- 5yr | 25yr | 100yr | ssa1..4 | susceptibility
    severity    text NOT NULL,
    pct_area    real NOT NULL,
    source_file text NOT NULL,
    PRIMARY KEY (psgc, hazard_type, scenario, severity)
);

-- Raw CAP audit trail: verbatim signed XML, never mutated
CREATE TABLE IF NOT EXISTS cap_alert (
    identifier  text PRIMARY KEY,
    sender      text NOT NULL,
    sent        timestamptz NOT NULL,
    status      text NOT NULL,
    msg_type    text NOT NULL,             -- Alert | Update | Cancel
    "references" text,
    event       text,
    severity    text,
    raw_xml     xml NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

-- Subscriptions: two tiers per barangay (see PLAN.md §2 Deliver)
CREATE TABLE IF NOT EXISTS subscriber (
    id          bigserial PRIMARY KEY,
    psgc        char(10) NOT NULL REFERENCES gazetteer,
    tier        text NOT NULL CHECK (tier IN ('bdrrmc', 'resident')),
    display_name text NOT NULL,
    msisdn      text,                      -- E.164; SMS
    viber_ref   text,                      -- community/admin handle
    telegram_id text,
    language    text NOT NULL DEFAULT 'ceb',
    consent_at  timestamptz NOT NULL,      -- Data Privacy Act: documented opt-in
    consent_ref text NOT NULL,             -- where/how consent was captured
    active      boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS subscriber_psgc ON subscriber (psgc) WHERE active;

-- One row per (alert, barangay) after fan-out
CREATE TABLE IF NOT EXISTS alert_instance (
    id          bigserial PRIMARY KEY,
    cap_identifier text NOT NULL REFERENCES cap_alert,
    psgc        char(10) NOT NULL REFERENCES gazetteer,
    matched_via text NOT NULL,             -- polygon | geocode | geocode-heuristic | name
    hazard_type text,
    max_pct_area real,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cap_identifier, psgc)
);

-- Delivery + acknowledgment event log: the RA 12287 audit timeline.
-- Append-only; the ack-loop escalation ladder is driven from this table.
CREATE TABLE IF NOT EXISTS delivery_event (
    id           bigserial PRIMARY KEY,
    alert_instance_id bigint NOT NULL REFERENCES alert_instance,
    subscriber_id bigint REFERENCES subscriber,
    channel      text NOT NULL,            -- sms | viber | telegram | messenger
    kind         text NOT NULL CHECK (kind IN
                   ('queued','sent','delivered','failed','ack','escalated')),
    detail       jsonb,
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS delivery_event_instance ON delivery_event (alert_instance_id, at);
