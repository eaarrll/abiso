from pathlib import Path

import polars as pl

from pipeline.downscale.fanout import Downscaler, hazard_type_for_event
from pipeline.ingest.cap import parse_cap

FIXTURES = Path(__file__).parent / "fixtures"


def _gazetteer() -> pl.DataFrame:
    # Minimal Region VII slice: Cebu province (old 9-digit 072200000,
    # new 10-digit prefix 07022) with two barangays, one namesake
    # barangay in another province to prove code-scoped matching.
    rows = [
        ("0700000000", "Region VII (Central Visayas)", "Reg"),
        ("0702200000", "Cebu", "Prov"),
        ("0702234001", "Poblacion", "Bgy"),
        ("0702234002", "San Roque", "Bgy"),
        ("1705200000", "Oriental Mindoro", "Prov"),
        ("1705234001", "Poblacion", "Bgy"),
    ]
    return pl.DataFrame(
        {
            "code": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "level": [r[2] for r in rows],
        }
    ).with_columns(
        region_code=pl.col("code").str.slice(0, 2),
        province_code=pl.col("code").str.slice(0, 5),
        citymun_code=pl.col("code").str.slice(0, 7),
        old_code=pl.lit(None, dtype=pl.String),
    )


def _hazard() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "psgc": ["0702234001", "0702234001", "0702234002"],
            "hazard_type": ["flood", "storm_surge", "flood"],
            "scenario": ["100yr", "ssa2", "100yr"],
            "severity": ["3", "2", "1"],
            "pct_area": [0.62, 0.10, 0.05],
            "source_file": ["t"] * 3,
        }
    )


def _downscaler() -> Downscaler:
    return Downscaler(
        gazetteer=_gazetteer(), barangay_hazard=_hazard(), adm4_path=Path("/nonexistent")
    )


def test_fanout_geocode_heuristic_and_name():
    alert = parse_cap((FIXTURES / "gfa_sample.cap").read_bytes())
    result = _downscaler().fan_out(alert)

    # Cebu resolves (SAME 072200000 -> heuristic 07022, and name "Cebu");
    # only Cebu barangays fan out — the Mindoro namesake "Poblacion" must not.
    codes = {h.psgc for h in result.hits}
    assert codes == {"0702234001", "0702234002"}

    # ranked by hazard susceptibility for the mapped hazard (flood)
    assert result.hits[0].psgc == "0702234001"
    assert result.hits[0].max_pct_area == 0.62
    assert result.hits[0].hazard_type == "flood"

    # the fictitious area must surface for a human, not vanish
    assert result.unresolved == ["Isla Ficticia"]


def test_fanout_exact_crosswalk_beats_heuristic():
    gaz = _gazetteer().with_columns(
        old_code=pl.when(pl.col("code") == "0702200000")
        .then(pl.lit("072200000"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )
    ds = Downscaler(gazetteer=gaz, barangay_hazard=_hazard(), adm4_path=Path("/nonexistent"))
    alert = parse_cap((FIXTURES / "gfa_sample.cap").read_bytes())
    result = ds.fan_out(alert)
    assert all(h.matched_via == "geocode" for h in result.hits)


def test_noah_path_classification():
    from pipeline.dataprep.hazard_intersect import classify

    assert classify("Flood/100yr/Cebu/Cebu_fh100yr.shp") == ("flood", "100yr")
    assert classify("Flood/5yr/Cebu/whatever.shp") == ("flood", "5yr")
    assert classify("Storm Surge/StormSurgeAdvisory2/Cebu/x.shp") == ("storm_surge", "ssa2")
    assert classify("Landslide/LandslideHazards/Cebu/x.shp") == ("landslide", "susceptibility")
    assert classify("ReadMe/notes.shp") is None


def test_hazard_type_mapping():
    assert hazard_type_for_event("General Flood Advisory") == "flood"
    assert hazard_type_for_event("Heavy Rainfall Warning") == "flood"
    assert hazard_type_for_event("Storm Surge Warning") == "storm_surge"
    assert hazard_type_for_event("Tropical Cyclone Warning") == "storm_surge"
    assert hazard_type_for_event("Volcanic Ash") is None
