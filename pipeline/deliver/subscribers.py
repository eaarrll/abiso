"""Subscriber admin CLI.

usage:
  python -m pipeline.deliver.subscribers add --psgc 0702219002 \\
      --tier bdrrmc --name "Kap. Juan Dela Cruz" --msisdn +639171234567 \\
      --language fil --consent-ref "onboarding form 2026-07-10, BDRRMC mtg"
  python -m pipeline.deliver.subscribers list [--psgc CODE]
  python -m pipeline.deliver.subscribers deactivate --id 3
"""

import argparse
from datetime import datetime, timezone

from pipeline.deliver.store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add")
    add.add_argument("--psgc", required=True, help="10-digit barangay PSGC code")
    add.add_argument("--tier", required=True, choices=["bdrrmc", "resident"])
    add.add_argument("--name", required=True)
    add.add_argument("--msisdn", help="E.164, e.g. +639171234567")
    add.add_argument("--telegram-id")
    add.add_argument("--language", default="fil", choices=["fil", "en", "ceb"])
    add.add_argument(
        "--consent-ref", required=True,
        help="where/how opt-in was documented (Data Privacy Act requirement)",
    )

    ls = sub.add_parser("list")
    ls.add_argument("--psgc")

    de = sub.add_parser("deactivate")
    de.add_argument("--id", type=int, required=True)

    args = ap.parse_args()
    store = Store()

    if args.cmd == "add":
        sid = store.add_subscriber(
            args.psgc, args.tier, args.name,
            msisdn=args.msisdn, telegram_id=args.telegram_id,
            language=args.language,
            consent_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            consent_ref=args.consent_ref,
        )
        print(f"added subscriber #{sid}")
    elif args.cmd == "list":
        q = "SELECT * FROM subscriber" + (" WHERE psgc = ?" if args.psgc else "")
        rows = store.conn.execute(q, (args.psgc,) if args.psgc else ()).fetchall()
        for r in rows:
            state = "active" if r["active"] else "inactive"
            print(f"#{r['id']} [{state}] {r['psgc']} {r['tier']:<8} {r['display_name']} "
                  f"lang={r['language']} msisdn={r['msisdn'] or '-'} "
                  f"tg={r['telegram_id'] or '-'}")
        print(f"{len(rows)} subscriber(s)")
    elif args.cmd == "deactivate":
        store.deactivate_subscriber(args.id)
        print(f"deactivated #{args.id}")


if __name__ == "__main__":
    main()
