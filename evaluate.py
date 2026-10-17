"""
evaluate.py - score the agent against the answer key (data/answer_key.json).

Run:  python evaluate.py            (uses LLM_PROVIDER from .env)
      python evaluate.py offline

For every flagged transaction it runs a full investigation and reports how many
ring members were caught (FREEZE/ESCALATE) and whether any innocent account was hit.
"""
import json
import sys

import agent
from db import run_query

key = json.load(open("data/answer_key.json"))
provider = sys.argv[1] if len(sys.argv) > 1 else None
innocent = {a for ids in key["innocent"].values() for a in ids}
total_caught = total_truth = false_alarms = 0

for txn_id, label in key["flagged_transactions"].items():
    run_query("MATCH (c:Case) DETACH DELETE c")          # start each case with a clean memory
    run_query("MATCH (a:Account) SET a.status = 'ACTIVE'")
    taken, tools_used, error = {}, 0, ""
    for e in agent.investigate(txn_id, provider):
        if e["type"] == "tool_result":
            tools_used += 1
            if e["tool"] == "take_action":
                for acc in e["result"]["data"].get("accounts", []):
                    taken[acc] = e["result"]["data"]["action"]
        if e["type"] == "error":
            error = e["text"][:80]
    acted = {a for a, act in taken.items() if act in ("FREEZE", "ESCALATE")}
    truth = set(key["rings"].get(label, []))
    wrong = acted - truth
    total_caught += len(acted & truth)
    total_truth += len(truth)
    false_alarms += len(wrong)
    verdict = ",".join(sorted(set(taken.values()))) or "-"
    print(f"{txn_id}  {label:22s} caught {len(acted & truth)}/{len(truth)}  false alarms {len(wrong)}  "
          f"actions {verdict:16s} tools {tools_used}  {error}")

print(f"\nRing members caught: {total_caught}/{total_truth}   False alarms: {false_alarms}")
