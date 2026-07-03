"""Pull NOAH hazard shapefiles (ODbL) for the pilot provinces.

Source is the bettergovph HuggingFace mirror of UP NOAH's hazard maps
(flood 5/25/100-yr, storm surge SSA1-4, landslide). The full dataset is
~34 GB; we filter by province name (NOAH_PROVINCES, default "Cebu").
"""

from huggingface_hub import list_repo_files, snapshot_download

from pipeline import config


def main() -> None:
    files = list_repo_files(config.NOAH_HF_REPO, repo_type="dataset")
    provinces_lc = [p.lower() for p in config.NOAH_PROVINCES]
    wanted = [
        f
        for f in files
        if any(p in f.lower() for p in provinces_lc)
        and not f.lower().endswith(".pmtiles")
    ]
    if not wanted:
        sample = "\n  ".join(files[:20])
        raise SystemExit(
            f"noah: no files match provinces {config.NOAH_PROVINCES}; "
            f"first repo entries:\n  {sample}"
        )
    print(f"noah: {len(wanted)} files match {config.NOAH_PROVINCES}, downloading…")

    local_dir = config.RAW / "noah"
    snapshot_download(
        config.NOAH_HF_REPO,
        repo_type="dataset",
        local_dir=local_dir,
        allow_patterns=[f"*{p}*" for p in config.NOAH_PROVINCES],
        ignore_patterns=["*.pmtiles"],
    )

    # Layers ship as {Hazard}/{Scenario}/{Province}.zip — extract next to the
    # zip so the hazard/scenario stays in the path for hazard_intersect.
    import zipfile

    for z in sorted(local_dir.rglob("*.zip")):
        out = z.with_suffix("")
        if not out.exists():
            print(f"noah: extracting {z.relative_to(local_dir)}")
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)

    shps = sorted(local_dir.rglob("*.shp"))
    print(f"noah: done — {len(shps)} shapefiles under {local_dir}")
    for s in shps:
        print(f"  {s.relative_to(local_dir)}")


if __name__ == "__main__":
    main()
