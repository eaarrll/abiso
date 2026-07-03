"""Fetch OCHA COD-AB PHL ADM4 (barangay) polygons -> GeoParquet.

Boundaries are indicative, not authoritative (PSGC-2023 vintage; barangay
limits are legally undemarcated in many LGUs). Fan-out is any-intersect
and over-inclusive by design, so indicative is acceptable. Filtered to
REGION (default 07) to keep the pilot artifact small.
"""

import re
import sys
import zipfile
from pathlib import Path

import requests

from pipeline import config


def _find_shp_resource() -> dict:
    r = requests.get(
        config.HDX_PACKAGE_URL, headers={"User-Agent": config.USER_AGENT}, timeout=120
    )
    r.raise_for_status()
    resources = r.json()["result"]["resources"]
    candidates = [
        res
        for res in resources
        if res.get("format", "").lower() in ("shp", "zipped shapefile", "zip")
        and "adm" in res.get("name", "").lower()
    ]
    if not candidates:
        sys.exit("boundaries: no ADM shapefile resource found on HDX cod-ab-phl")
    return candidates[0]


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, headers={"User-Agent": config.USER_AGENT}, timeout=600, stream=True
    ) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def main() -> None:
    import geopandas as gpd

    zip_path = config.RAW / "cod_ab_phl.zip"
    if not zip_path.exists():
        res = _find_shp_resource()
        print(f"boundaries: downloading {res['name']} ({res.get('size', '?')} bytes)")
        _download(res["url"], zip_path)
    else:
        print(f"boundaries: reusing {zip_path}")

    # Release naming varies: *_adm4_* (2023) vs phl_admin4.* (2026).
    adm4_rx = re.compile(r"adm(?:in)?_?4", re.I)
    extract_dir = config.RAW / "cod_ab_phl"
    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.namelist() if adm4_rx.search(Path(m).name)]
        if not members:
            sys.exit(f"boundaries: no ADM4 members inside {zip_path}: {z.namelist()[:10]}")
        z.extractall(extract_dir, members=members)

    adm4_shps = sorted(p for p in extract_dir.rglob("*.shp") if adm4_rx.search(p.name))
    if not adm4_shps:
        sys.exit(f"boundaries: no ADM4 shapefile extracted to {extract_dir}")
    gdf = gpd.read_file(adm4_shps[0])
    print(f"boundaries: {len(gdf)} ADM4 features, columns: {list(gdf.columns)}")

    pcode_col = next((c for c in gdf.columns if c.upper() == "ADM4_PCODE"), None)
    name_col = next((c for c in gdf.columns if c.upper() == "ADM4_EN"), None)
    if pcode_col is None:
        sys.exit("boundaries: ADM4_PCODE column missing")

    # P-codes are 'PH' + PSGC digits; normalize to the bare 10-digit key.
    gdf["psgc"] = (
        gdf[pcode_col].astype(str).str.removeprefix("PH").str.strip().str.zfill(10)
    )
    gdf["bgy_name"] = gdf[name_col] if name_col else ""
    gdf = gdf[gdf["psgc"].str.startswith(config.REGION)][
        ["psgc", "bgy_name", "geometry"]
    ]
    if gdf.empty:
        sys.exit(
            f"boundaries: 0 features for region {config.REGION} — "
            "check P-code layout in the COD-AB release"
        )
    gdf = gdf.to_crs(4326)

    config.WAREHOUSE.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(config.ADM4_PARQUET)
    print(f"boundaries: wrote {len(gdf)} region-{config.REGION} barangays -> {config.ADM4_PARQUET}")


if __name__ == "__main__":
    main()
