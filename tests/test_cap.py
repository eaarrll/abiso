from pathlib import Path

from pipeline.ingest.cap import parse_atom, parse_cap

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_gfa_sample():
    alert = parse_cap((FIXTURES / "gfa_sample.cap").read_bytes())

    assert alert.identifier == "PAGASA-GFA-TEST-0001"
    assert alert.msg_type == "Alert"
    assert alert.status == "Actual"

    assert len(alert.infos) == 1
    info = alert.infos[0]
    assert info.event == "General Flood Advisory"
    assert info.severity == "Extreme"
    assert ("Rainfall Warning Level", "Red") in info.parameters

    assert len(info.areas) == 2
    cebu = info.areas[0]
    assert cebu.area_desc == "Cebu"
    assert cebu.geocodes == [("SAME", "072200000")]
    assert len(cebu.polygons) == 1
    assert cebu.polygons[0][0] == (10.20, 123.50)  # lat, lon order


def test_parse_polygon_garbage_dropped():
    xml = (FIXTURES / "gfa_sample.cap").read_bytes().replace(
        b"10.20,123.50 10.20,124.10 11.30,124.10 11.30,123.50 10.20,123.50",
        b"not,a 10.2 polygon",
    )
    alert = parse_cap(xml)
    assert alert.infos[0].areas[0].polygons == []


def test_parse_atom():
    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>PAGASA CAP</title>
      <entry>
        <id>urn:uuid:abc</id>
        <updated>2026-07-02T12:00:00Z</updated>
        <title>General Flood Advisory</title>
        <link href="output/gfa/abc-def.cap"/>
      </entry>
      <entry>
        <id>urn:uuid:xyz</id>
        <updated>2026-07-02T13:00:00Z</updated>
        <title>No CAP link here</title>
        <link href="https://example.com/page.html"/>
      </entry>
    </feed>"""
    entries = parse_atom(atom)
    assert len(entries) == 2
    assert entries[0].cap_url == "output/gfa/abc-def.cap"
    assert entries[0].title == "General Flood Advisory"
    assert entries[1].cap_url == ""
