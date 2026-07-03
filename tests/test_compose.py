from pathlib import Path

import pytest

from pipeline.compose.engine import (
    ComposeError,
    TemplateBook,
    gsm_septets,
    sms_segments,
)
from pipeline.ingest.cap import parse_cap

FIXTURES = Path(__file__).parent / "fixtures"
SLOTS = {"bgy": "Brgy Cansaga", "evac": "Cansaga Barangay Hall"}


@pytest.fixture(scope="module")
def book() -> TemplateBook:
    return TemplateBook()  # the real templates/ dir — tests ARE the template CI


@pytest.fixture(scope="module")
def gfa_extreme():
    return parse_cap((FIXTURES / "gfa_sample.cap").read_bytes())


def test_select_flood_extreme(book, gfa_extreme):
    assert book.select(gfa_extreme)["id"] == "flood_extreme"


def test_cancel_outranks_everything(book):
    xml = (FIXTURES / "gfa_sample.cap").read_bytes().replace(
        b"<msgType>Alert</msgType>", b"<msgType>Cancel</msgType>"
    )
    assert book.select(parse_cap(xml))["id"] == "all_clear"


def test_severity_fallback(book):
    xml = (FIXTURES / "gfa_sample.cap").read_bytes().replace(
        b"<severity>Extreme</severity>", b"<severity>Moderate</severity>"
    )
    assert book.select(parse_cap(xml))["id"] == "flood_watch"


def test_render_en_approved(book, gfa_extreme):
    t = book.select(gfa_extreme)
    r = book.render(gfa_extreme, t, "en", slots=SLOTS)
    assert "PAGASA" in r.text
    assert "Cansaga Barangay Hall" in r.text
    assert "Reply YES" in r.text  # bdrrmc tier gets the ack request
    assert "http" not in r.text.lower()
    assert r.segments == 1
    # bulletin timestamp rendered in PH time (sent 20:00+08:00 -> 8PM)
    assert "8PM" in r.text


def test_resident_tier_has_no_ack(book, gfa_extreme):
    t = book.select(gfa_extreme)
    r = book.render(gfa_extreme, t, "en", slots=SLOTS, tier="resident")
    assert "Reply" not in r.text
    assert not r.text.endswith(" ")


def test_fil_is_live_sendable(book, gfa_extreme):
    """fil was human-approved (reviewed_by recorded in the YAML) on
    2026-07-04 — it must render without allow_draft, like en."""
    t = book.select(gfa_extreme)
    r = book.render(gfa_extreme, t, "fil", slots=SLOTS)
    assert "Malubhang baha" in r.text
    assert not r.warnings


def test_draft_language_refuses_without_flag(book, gfa_extreme):
    t = book.select(gfa_extreme)
    with pytest.raises(ComposeError, match="human validation"):
        book.render(gfa_extreme, t, "ceb", slots=SLOTS)
    r = book.render(gfa_extreme, t, "ceb", slots=SLOTS, allow_draft=True)
    assert "grabeng pagbaha" in r.text
    assert any("DRAFT" in w for w in r.warnings)


def test_missing_slot_fails_closed(book, gfa_extreme):
    t = book.select(gfa_extreme)
    with pytest.raises(ComposeError, match="missing slot"):
        book.render(gfa_extreme, t, "en", slots={"bgy": "Brgy Cansaga"})  # no evac


def test_unmatched_alert_raises(book):
    xml = (
        (FIXTURES / "gfa_sample.cap")
        .read_bytes()
        .replace(b"General Flood Advisory", b"Volcanic Ashfall")
    )
    with pytest.raises(ComposeError, match="human"):
        book.select(parse_cap(xml))


def test_all_templates_all_languages_render(book, gfa_extreme):
    """Every template x language must slot-fill cleanly and carry the
    attribution — catches broken placeholders before a translator or,
    worse, a live event does."""
    for t in book.templates:
        for lang in t["text"]:
            r = book.render(gfa_extreme, t, lang, slots=SLOTS, allow_draft=True)
            assert r.text.startswith("PAGASA "), (t["id"], lang)
            assert "{" not in r.text and "}" not in r.text, (t["id"], lang)
            assert gsm_septets(r.text) is not None, (t["id"], lang, "non-GSM chars")
            assert r.segments <= 2, (t["id"], lang, r.segments, r.text)


def test_pilot_languages_fit_one_segment(book, gfa_extreme):
    """en + fil are the pilot's live languages: with realistic slot values
    every template must fit a single SMS segment (cost + delivery
    reliability). ceb is deferred and exempt until its human translation
    pass."""
    for t in book.templates:
        for lang in ("en", "fil"):
            r = book.render(gfa_extreme, t, lang, slots=SLOTS, allow_draft=True)
            assert r.segments == 1, (t["id"], lang, gsm_septets(r.text), r.text)


def test_gsm_counting():
    assert gsm_septets("abc") == 3
    assert gsm_septets("[]") == 4  # extension table chars cost 2
    assert gsm_septets("ñ ulan") == 6  # ñ is GSM basic
    assert gsm_septets("波") is None  # forces UCS-2
    assert sms_segments("x" * 160) == 1
    assert sms_segments("x" * 161) == 2
