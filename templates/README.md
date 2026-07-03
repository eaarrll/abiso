# Alert templates

One YAML document per hazard family; each `templates:` entry is one
parameterized message (hazard × severity × action) per PLAN.md §2.

## Rules (non-negotiable)

- `en` is the source text. `fil`/`ceb` entries start as
  `status: needs_review` drafts and MUST be professionally translated and
  community-validated with pilot BDRRMCs before being set to
  `status: approved`. The engine refuses to render non-approved languages
  outside tests. **Never edit a translation to `approved` without a named
  human reviewer** — record the reviewer in `reviewed_by`.
- No URLs (carriers block them), no predictive numbers or claims that are
  not in the PAGASA bulletin, advisory phrasing only — never "order".
- Slots (`{bgy}`, `{evac}`) pass through untranslated. `{attrib}` and
  `{ack}` are engine-provided.
- Controlled vocabulary: PAGASA/KWF terms (e.g. *daluyong* = storm surge;
  Cebuano *dagkong balod*).
- SMS bodies should render ≤160 GSM septets with realistic slot values;
  the engine warns when they don't. Avoid [ ] { } ~ ^ \\ | — each costs
  2 septets.

## Matching

Templates are tried in descending `priority`. `match` supports:
`msg_type` (exact), `event_rx` / `param.name_rx` / `param.value_rx`
(regex, case-insensitive), `severity` (string or list, exact CAP values).
Fallback templates (no `severity`) carry lower priority than their
severity-specific siblings. An alert matching nothing raises — by design,
it pages a human instead of guessing.
