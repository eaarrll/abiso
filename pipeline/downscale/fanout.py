"""Downscale a CAP alert to barangays — the relay's core value-add.

No official source issues barangay-level alerts; PAGASA CAP areas carry
province-level geocodes plus (sometimes partial-province) polygons. This
module resolves those areas to 10-digit PSGC barangay codes three ways,
most-precise first, and unions the results (over-inclusion is the safe
failure mode for warnings):

1. polygon  — CAP polygon ANY-intersects a barangay boundary (needs the
              ADM4 GeoParquet)
2. geocode  — SAME value (old 9-digit province PSGC) via the PSA
              crosswalk when the gazetteer has it, else the RRPP -> RR0PP
              recode heuristic (correct for most provinces, wrong where
              2023 renumbered — hence marked low-confidence)
3. name     — areaDesc matched against province names + alias table

Every hit is annotated from barangay_hazard so compose can rank.
An area that resolves to nothing is returned in `unresolved` — that must
page a human, never be silently dropped.
"""

import csv
import json
import re
import sys
from dataclasses import dataclass, field

import polars as pl

from pipeline import config
from pipeline.ingest.cap import CapAlert, parse_cap

# CAP event text -> barangay_hazard.hazard_type
_EVENT_HAZARD = [
    (re.compile(r"surge", re.I), "storm_surge"),
    (re.compile(r"flood|rain|thunder", re.I), "flood"),
    (re.compile(r"landslide", re.I), "landslide"),
    (re.compile(r"typhoon|cyclone|storm|wind", re.I), "storm_surge"),
]


def hazard_type_for_event(event: str) -> str | None:
    for rx, hazard in _EVENT_HAZARD:
        if rx.search(event):
            return hazard
    return None


def _norm(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name.lower())
    name = re.sub(r"[^a-z0-9ñ ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _load_aliases() -> dict[str, str]:
    path = config.REFERENCE / "province_aliases.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {row["alias"]: row["canonical"] for row in csv.DictReader(f)}


@dataclass
class BarangayHit:
    psgc: str
    name: str
    province_code: str
    matched_via: str  # polygon | geocode | geocode-heuristic | name
    hazard_type: str | None = None
    max_severity: str | None = None
    max_pct_area: float = 0.0


@dataclass
class FanoutResult:
    alert_id: str
    event: str
    severity: str
    hits: list[BarangayHit] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # areaDesc needing a human


class Downscaler:
    def __init__(
        self,
        gazetteer: pl.DataFrame | None = None,
        barangay_hazard: pl.DataFrame | None = None,
        adm4_path=None,
    ):
        self.gaz = (
            gazetteer
            if gazetteer is not None
            else pl.read_parquet(config.GAZETTEER_PARQUET)
        )
        self.hazard = barangay_hazard
        if self.hazard is None and config.BARANGAY_HAZARD_PARQUET.exists():
            self.hazard = pl.read_parquet(config.BARANGAY_HAZARD_PARQUET)
        self.aliases = _load_aliases()

        provs = self.gaz.filter(pl.col("level").is_in(["Prov", "Reg"]))
        self.prov_by_name = {
            _norm(n): c for n, c in zip(provs["name"], provs["province_code"])
        }
        if "old_code" in self.gaz.columns:
            xw = self.gaz.filter(pl.col("old_code").is_not_null())
            self.old_to_new = dict(zip(xw["old_code"], xw["code"]))
        else:
            self.old_to_new = {}

        self._adm4 = None
        self._adm4_path = adm4_path or config.ADM4_PARQUET

    # --- resolution paths -------------------------------------------------

    def _provinces_from_geocode(self, value: str) -> tuple[set[str], str]:
        """SAME 9-digit province code -> 5-digit new province prefixes."""
        value = value.strip()
        if not re.fullmatch(r"\d{9}", value):
            return set(), "geocode"
        if value in self.old_to_new:
            return {self.old_to_new[value][:5]}, "geocode"
        # Heuristic recode: RRPPMMBBB -> RR + 0PP. Correct for provinces
        # that kept their number through the 2023 10-digit migration.
        guess = value[:2] + "0" + value[2:4]
        if not self.gaz.filter(pl.col("province_code") == guess).is_empty():
            return {guess}, "geocode-heuristic"
        return set(), "geocode"

    def _provinces_from_name(self, area_desc: str) -> set[str]:
        found = set()
        for chunk in re.split(r"[;,]| and ", area_desc, flags=re.I):
            key = _norm(chunk)
            if not key:
                continue
            key = _norm(self.aliases.get(key, key))
            if key in self.prov_by_name:
                found.add(self.prov_by_name[key])
        return found

    def _barangays_from_polygon(self, polygons) -> set[str]:
        if not self._adm4_path or not self._adm4_path.exists():
            return set()
        if self._adm4 is None:
            import geopandas as gpd

            self._adm4 = gpd.read_parquet(self._adm4_path)
        from shapely.geometry import Polygon

        hit = set()
        for pts in polygons:
            if len(pts) < 3:
                continue
            poly = Polygon([(lon, lat) for lat, lon in pts])  # CAP is lat,lon
            if not poly.is_valid:
                poly = poly.buffer(0)
            mask = self._adm4.geometry.intersects(poly)
            hit.update(self._adm4.loc[mask, "psgc"])
        return hit

    # --- main entry ---------------------------------------------------------

    def fan_out(self, alert: CapAlert) -> FanoutResult:
        info = alert.infos[0] if alert.infos else None
        result = FanoutResult(
            alert_id=alert.identifier,
            event=info.event if info else "",
            severity=info.severity if info else "",
        )
        if info is None:
            return result

        bgy_via: dict[str, str] = {}  # psgc -> matched_via (first, most precise)
        bgys = self.gaz.filter(pl.col("level") == "Bgy")

        for area in info.areas:
            matched = False

            for psgc in self._barangays_from_polygon(area.polygons):
                bgy_via.setdefault(psgc, "polygon")
                matched = True

            prov_prefixes: dict[str, str] = {}
            for _, value in area.geocodes:
                prefixes, via = self._provinces_from_geocode(value)
                for p in prefixes:
                    prov_prefixes.setdefault(p, via)
            for p in self._provinces_from_name(area.area_desc):
                prov_prefixes.setdefault(p, "name")

            for prefix, via in prov_prefixes.items():
                for psgc in bgys.filter(pl.col("province_code") == prefix)["code"]:
                    bgy_via.setdefault(psgc, via)
                matched = True

            if not matched:
                result.unresolved.append(area.area_desc)

        if not bgy_via:
            return result

        sub = bgys.filter(pl.col("code").is_in(list(bgy_via)))
        hazard_type = hazard_type_for_event(info.event)
        hazard_lookup = {}
        if self.hazard is not None and hazard_type:
            hz = (
                self.hazard.filter(pl.col("hazard_type") == hazard_type)
                .group_by("psgc")
                .agg(
                    pl.col("severity").max().alias("max_severity"),
                    pl.col("pct_area").max().alias("max_pct_area"),
                )
            )
            hazard_lookup = {
                r["psgc"]: r for r in hz.iter_rows(named=True)
            }

        for row in sub.iter_rows(named=True):
            hz = hazard_lookup.get(row["code"])
            result.hits.append(
                BarangayHit(
                    psgc=row["code"],
                    name=row["name"],
                    province_code=row["province_code"],
                    matched_via=bgy_via[row["code"]],
                    hazard_type=hazard_type,
                    max_severity=str(hz["max_severity"]) if hz else None,
                    max_pct_area=float(hz["max_pct_area"]) if hz else 0.0,
                )
            )
        result.hits.sort(key=lambda h: h.max_pct_area, reverse=True)
        return result


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m pipeline.downscale.fanout <alert.xml>")
    alert = parse_cap(open(sys.argv[1], "rb").read())
    result = Downscaler().fan_out(alert)
    print(
        json.dumps(
            {
                "alert_id": result.alert_id,
                "event": result.event,
                "severity": result.severity,
                "barangays": len(result.hits),
                "unresolved_areas": result.unresolved,
                "top": [vars(h) for h in result.hits[:10]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.unresolved:
        sys.exit(2)  # unresolved area = a human must look


if __name__ == "__main__":
    main()
