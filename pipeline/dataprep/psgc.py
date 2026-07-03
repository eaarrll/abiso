"""Build the PSGC gazetteer -> warehouse/gazetteer.parquet.

Primary source: psgc.gitlab.io API (mirrors PSA, lags by weeks). If
PSGC_XLSX points at the PSA quarterly publication file, rows are enriched
with the correspondence (old 9-digit) codes needed to resolve CAP SAME
geocodes exactly. Barangays are keyed by 10-digit PSGC code — never by
name (thousands of namesakes).

10-digit layout RRPPPMMBBB: region [0:2], province [2:5], city/muni [5:7],
barangay [7:10].
"""

import sys

import polars as pl
import requests

from pipeline import config


def _fetch(endpoint: str) -> list[dict]:
    url = f"{config.PSGC_API_BASE}/{endpoint}.json"
    r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=120)
    r.raise_for_status()
    return r.json()


def _rows_from_api() -> pl.DataFrame:
    """The API carries both code generations per row: `code` is the old
    9-digit PSGC (what PAGASA CAP SAME geocodes use) and `psgc10DigitCode`
    is the current 10-digit key we join boundaries/hazard on."""
    rows = []
    for endpoint, level in [
        ("regions", "Reg"),
        ("provinces", "Prov"),
        ("cities-municipalities", "City/Mun"),
        ("barangays", "Bgy"),
    ]:
        items = _fetch(endpoint)
        print(f"psgc api: {len(items)} {endpoint}")
        for it in items:
            code10 = str(it.get("psgc10DigitCode", "") or "").strip()
            code9 = str(it.get("code", "") or "").strip()
            if not code10:
                continue
            rows.append(
                {
                    "code": code10,
                    "name": it.get("name", ""),
                    "level": level,
                    "old_code": code9.zfill(9) if code9 else None,
                }
            )
    return pl.DataFrame(rows)


def _enrich_from_psa_xlsx(df: pl.DataFrame, xlsx_path: str) -> pl.DataFrame:
    """Join old-code crosswalk from the PSA publication file.

    Column names vary slightly by quarter, so match by substring.
    """
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, read_only=True)
    ws = max(wb.worksheets, key=lambda w: w.max_row)  # PSGC sheet is the big one
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(rows)]

    def col(*needles: str) -> int | None:
        for i, h in enumerate(header):
            if all(n in h for n in needles):
                return i
        return None

    c_code = col("10-digit") or col("psgc")
    c_old = col("correspondence")
    if c_code is None or c_old is None:
        print(f"psgc: could not locate code columns in {xlsx_path}; header={header}")
        return df

    crosswalk = []
    for r in rows:
        code = str(r[c_code] or "").strip().split(".")[0]
        old = str(r[c_old] or "").strip().split(".")[0]
        if code and old:
            crosswalk.append({"code": code.zfill(10), "psa_old_code": old.zfill(9)})
    xw = pl.DataFrame(crosswalk).unique(subset="code")
    print(f"psgc: crosswalk rows from PSA file: {len(xw)}")
    # PSA is authoritative where it disagrees with the API mirror.
    return (
        df.join(xw, on="code", how="left")
        .with_columns(old_code=pl.coalesce("psa_old_code", "old_code"))
        .drop("psa_old_code")
    )


def main() -> None:
    df = _rows_from_api()
    if df.is_empty():
        sys.exit("psgc: API returned nothing")

    # Codes come back 9- or 10-digit depending on mirror vintage; the
    # 10-digit form is the only one we key on downstream.
    df = df.with_columns(pl.col("code").str.zfill(10))
    df = df.with_columns(
        region_code=pl.col("code").str.slice(0, 2),
        province_code=pl.col("code").str.slice(0, 5),
        citymun_code=pl.col("code").str.slice(0, 7),
    )

    if config.PSGC_XLSX:
        df = _enrich_from_psa_xlsx(df, config.PSGC_XLSX)
    n_old = df.filter(pl.col("old_code").is_not_null()).height
    print(f"psgc: old-code crosswalk coverage: {n_old}/{df.height}")

    config.WAREHOUSE.mkdir(parents=True, exist_ok=True)
    df.write_parquet(config.GAZETTEER_PARQUET)
    n_bgy = df.filter(pl.col("level") == "Bgy").height
    print(f"psgc: wrote {df.height} rows ({n_bgy} barangays) -> {config.GAZETTEER_PARQUET}")


if __name__ == "__main__":
    main()
