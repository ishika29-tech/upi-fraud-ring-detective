"""
tests/test_rings.py - checks that the agent (offline mode, no API key needed)
finds every planted fraud ring and does NOT freeze the innocent accounts.

Run:  python generate_data.py && pytest -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from db import run_query  # noqa: E402

KEY = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "answer_key.json")))


def actions_for(txn_id):
    """Run the offline agent and return {account_id: action}."""
    run_query("MATCH (c:Case) DETACH DELETE c")
    run_query("MATCH (a:Account) SET a.status = 'ACTIVE'")
    taken = {}
    for event in agent.investigate(txn_id, provider="offline"):
        if event["type"] == "tool_result" and event["tool"] == "take_action":
            for acc in event["result"]["data"]["accounts"]:
                taken[acc] = event["result"]["data"]["action"]
    return taken


def test_every_ring_is_found():
    for txn_id, ring in KEY["flagged_transactions"].items():
        if ring not in KEY["rings"]:
            continue
        taken = actions_for(txn_id)
        caught = {a for a, act in taken.items() if act in ("FREEZE", "ESCALATE")}
        truth = set(KEY["rings"][ring])
        recall = len(caught & truth) / len(truth)
        print(f"{ring}: caught {len(caught & truth)}/{len(truth)}, false alarms {len(caught - truth)}")
        assert recall >= 0.8, f"{ring}: only caught {caught & truth} of {truth}"
        assert not (caught - truth), f"{ring}: wrongly acted on {caught - truth}"


def test_innocent_accounts_are_not_frozen():
    for txn_id, label in KEY["flagged_transactions"].items():
        if not label.startswith("innocent"):
            continue
        taken = actions_for(txn_id)
        assert "FREEZE" not in taken.values(), f"{label}: froze {taken}"
