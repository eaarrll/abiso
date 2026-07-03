"""NOAH hazard polygons x ADM4 barangays -> warehouse/barangay_hazard.parquet.

One row per (barangay psgc, hazard_type, scenario, severity) with the
fraction of barangay area intersected. NOAH's own methodology counts a
barangay as prone on ANY intersection; we keep pct_area so the compose
stage can rank, but fan-out stays over-inclusive (any-intersect).

Hazard type and scenario are inferred from the mirror's directory layout
({Hazard}/{Scenario}/{Province}/…, e.g. Flood/100yr/Cebu,
Storm Surge/StormSurgeAdvisory2/Cebu, Landslide/LandslideHazards/Cebu).
Severity comes from the per-polygon hazard-level attribute when one
exists (Var/HAZ/GRIDCODE), else 0 (= present, unclassified).
"""

import re
from pathlib import Path

import polars as pl

from pipeline import config

# Equal-area CRS for area ratios (global cylindrical equal area).
_AREA_CRS = 6933

_SEVERITY_COLS = ("Var", "VAR", "HAZ", "haz", "GRIDCODE", "gridcode", "LH", "SS")


def classify(rel_path: str) -> tuple[str, str] | None:
    s = rel_path.lower()
    if "landslide" in s or re.search(r"(^|[_/])lh([_/.]|$)", s):
        return "landslide", "susceptibility"
    m = re.search(r"(?:stormsurgeadvisory|surge advisory|ssa)\s*_?(\d)", s)
    if m:
        return "storm_surge", f"ssa{m.group(1)}"
    if "surge" in s:
        return "storm_surge", "unspecified"
    if "flood" in s or re.search(r"(^|[_/])fh([_/.]|$)", s):
        m = re.search(r"(\d+)\s*yr", s)
        return "flood", f"{m.group(1)}yr" if m else "unspecified"
    return None


def intersect_one(shp: Path, adm4, hazard: str, scenario: str) -> pl.DataFrame | None:
    import geopandas as gpd

    haz = gpd.read_file(shp)
    if haz.empty:
        return None
    if haz.crs is None:
        haz = haz.set_crs(4326)
    haz = haz.to_crs(adm4.crs)

    sev_col = next((c for c in _SEVERITY_COLS if c in haz.columns), None)
    haz["severity"] = haz[sev_col] if sev_col else 0
    haz = haz[["severity", "geometry"]]

    joined = gpd.overlay(adm4, haz, how="intersection", keep_geom_type=True)
    if joined.empty:
        return None
    joined = joined.to_crs(_AREA_CRS)
    joined["part_area"] = joined.geometry.area

    df = pl.from_pandas(joined[["psgc", "bgy_name", "severity", "part_area"]])
    return (
        df.group_by(["psgc", "bgy_name", "severity"])
        .agg(pl.col("part_area").sum())
        .with_columns(
            hazard_type=pl.lit(hazard),
            scenario=pl.lit(scenario),
            source_file=pl.lit(shp.name),
        )
    )


def main() -> None:
    import geopandas as gpd

    adm4 = gpd.read_parquet(config.ADM4_PARQUET)
    adm4_area = adm4.to_crs(_AREA_CRS)
    adm4["bgy_area"] = adm4_area.geometry.area
    bgy_area = pl.from_pandas(adm4[["psgc", "bgy_area"]])

    noah_dir = config.RAW / "noah"
    shps = sorted(noah_dir.rglob("*.shp"))
    if not shps:
        raise SystemExit(f"hazard: no shapefiles under {noah_dir} — run `make noah` first")

    frames = []
    for shp in shps:
        cls = classify(str(shp.relative_to(noah_dir)))
        if cls is None:
            print(f"hazard: SKIP (unclassified path): {shp.relative_to(noah_dir)}")
            continue
        hazard, scenario = cls
        print(f"hazard: {shp.name} -> {hazard}/{scenario}")
        out = intersect_one(shp, adm4[["psgc", "bgy_name", "geometry"]], hazard, scenario)
        if out is not None:
            frames.append(out)

    if not frames:
        raise SystemExit("hazard: nothing intersected — check region filter vs provinces")

    result = (
        pl.concat(frames)
        .join(bgy_area, on="psgc")
        .with_columns(
            pct_area=(pl.col("part_area") / pl.col("bgy_area")).clip(0.0, 1.0)
        )
        .drop("part_area", "bgy_area")
        .sort(["psgc", "hazard_type", "scenario", "severity"])
    )
    result.write_parquet(config.BARANGAY_HAZARD_PARQUET)
    print(
        f"hazard: wrote {result.height} rows, "
        f"{result['psgc'].n_unique()} barangays -> {config.BARANGAY_HAZARD_PARQUET}"
    )


if __name__ == "__main__":
    main()
