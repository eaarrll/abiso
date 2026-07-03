"""Ack-loop tick: match new replies, then fire due escalations.
Run from cron every ~5 minutes alongside the poller:

  */5 * * * *  make poll dispatch ackloop
"""

from pipeline.ack.escalate import run_escalations
from pipeline.ack.match import match_inbound
from pipeline.deliver.dispatch import load_profiles
from pipeline.deliver.store import Store


def main() -> None:
    store = Store()
    m = match_inbound(store)
    print(f"ackloop: {m['acked']} acked, {m['ignored']} ignored")
    for reply in m["needs_human"]:
        print(f"ackloop: NEEDS HUMAN — {reply['from']} ({reply['psgc']}) "
              f"via {reply['channel']}: {reply['text']!r}")

    fired = run_escalations(store, load_profiles())
    for f in fired:
        print(f"ackloop: escalated {f['step']} for instance {f['instance']} "
              f"({f['psgc']}) at {f['elapsed_min']} min")
    if not fired:
        print("ackloop: no escalations due")


if __name__ == "__main__":
    main()
