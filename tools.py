"""
tools.py - the agent's TOOLS.

Each tool is a normal Python function that runs one Cypher query (or one
FalkorDB graph algorithm) and returns a dict:

    {
      "summary": "one sentence the LLM (and you) can read",
      "data":    {...the facts found...},
      "cypher":  "the exact Cypher that was run (shown in the UI for explainability)",
      "graph":   {"nodes": [...], "edges": [...]}   # what to draw in the UI
    }

The LLM never writes Cypher itself. It only chooses WHICH tool to call and with
WHAT arguments. That keeps the agent safe, fast and explainable.
"""
import json
import time
from datetime import date, timedelta

from db import run_query

# Which property identifies each kind of node (used for drawing the graph)
KEY_PROP = {"Account": "id", "UPI": "vpa", "Phone": "number", "Device": "id", "IP": "addr", "Case": "id"}


# ----------------------------------------------------------------------------
# Small helpers (not tools)
# ----------------------------------------------------------------------------
def rupees(x):
    """185000 -> '₹1,85,000'  (Indian lakh/crore comma style)"""
    digits = str(round(x or 0))
    last3, rest = digits[-3:], digits[:-3]
    groups = []
    while rest:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    return "₹" + ",".join(groups + [last3])


def when(ts):
    return time.strftime("%d %b %H:%M", time.localtime(ts)) if ts else "-"


def age_days(opened_on):
    return (date.today() - date.fromisoformat(opened_on)).days


def node_info(node):
    """Turn a FalkorDB node into a small dict the UI can draw."""
    label = node.labels[0]
    props = node.properties
    value = props.get(KEY_PROP.get(label, "id"), node.id)
    caption = f"{props.get('holder')}\n{value}" if label == "Account" else str(value)
    return {"id": f"{label}:{value}", "type": label, "caption": caption, "props": props}


def paths_to_graph(paths):
    """Collect the nodes and edges of a list of FalkorDB Path objects."""
    nodes, edges = {}, []
    for p in paths:
        if p is None:
            continue
        by_internal_id = {}
        for n in p.nodes():
            info = node_info(n)
            nodes[info["id"]] = info
            by_internal_id[n.id] = info["id"]
        for e in p.edges():
            edges.append({"from": by_internal_id[e.src_node], "to": by_internal_id[e.dest_node],
                          "type": e.relation, "props": e.properties})
    return {"nodes": list(nodes.values()), "edges": edges}


def result(summary, data, cypher, paths=()):
    return {"summary": summary, "data": data, "cypher": cypher.strip(), "graph": paths_to_graph(paths)}


def for_llm(tool_result, max_chars=6000):
    """The LLM only needs the summary + data (not the drawing info)."""
    text = json.dumps({"summary": tool_result["summary"], "data": tool_result["data"]}, default=str)
    return text[:max_chars]


# ----------------------------------------------------------------------------
# TOOL 0: list flagged transactions (where an investigation starts)
# ----------------------------------------------------------------------------
def list_flagged_transactions(limit: int = 20):
    """List recent transactions that were flagged (victim complaint or bank rule)."""
    cypher = """
MATCH (a:Account)-[t:PAID]->(b:Account)
WHERE t.flagged = true
RETURN t.txn_id AS txn_id, t.amount AS amount, t.ts AS ts, t.flag_reason AS reason,
       a.id AS from_id, a.holder AS from_name, b.id AS to_id, b.holder AS to_name
ORDER BY t.ts DESC LIMIT $limit"""
    rows = run_query(cypher, {"limit": int(limit)})
    for r in rows:
        r["when"] = when(r["ts"])
        r["amount_text"] = rupees(r["amount"])
    return result(f"{len(rows)} flagged transactions.", rows, cypher)


# ----------------------------------------------------------------------------
# TOOL 1: look at one transaction
# ----------------------------------------------------------------------------
def get_transaction(txn_id: str):
    """Who paid whom, how much, when, and why it was flagged."""
    cypher = """
MATCH p = (a:Account)-[t:PAID {txn_id: $txn_id}]->(b:Account)
RETURN p, a.id AS from_id, a.holder AS from_name, b.id AS to_id, b.holder AS to_name,
       t.amount AS amount, t.ts AS ts, t.flagged AS flagged, t.flag_reason AS reason"""
    rows = run_query(cypher, {"txn_id": txn_id})
    if not rows:
        return result(f"No transaction {txn_id} found.", {}, cypher)
    r = rows[0]
    data = {k: r[k] for k in ["from_id", "from_name", "to_id", "to_name", "amount", "flagged", "reason"]}
    data["when"] = when(r["ts"])
    summary = (f"{txn_id}: {r['from_name']} ({r['from_id']}) paid {rupees(r['amount'])} to "
               f"{r['to_name']} ({r['to_id']}) on {data['when']}. Flag: {r['reason'] or 'none'}.")
    return result(summary, data, cypher, [r["p"]])


# ----------------------------------------------------------------------------
# TOOL 2: profile of one account (1 hop: its UPI IDs, phone, devices, IPs)
# ----------------------------------------------------------------------------
def get_account_profile(account_id: str):
    """Account details, its UPI IDs, phone numbers, devices, IPs and money in/out."""
    cypher = """
MATCH (a:Account {id: $id})
OPTIONAL MATCH p = (a)-[:HAS_UPI|HAS_PHONE|USES_DEVICE]->()
OPTIONAL MATCH q = (a)-[:USES_DEVICE]->(:Device)-[:SEEN_ON]->(:IP)
RETURN a, collect(DISTINCT p) AS ps, collect(DISTINCT q) AS qs"""
    money_cypher = """
MATCH (a:Account {id: $id})
OPTIONAL MATCH (a)-[o:PAID]->()
WITH a, count(o) AS out_count, sum(o.amount) AS out_total
OPTIONAL MATCH ()-[i:PAID]->(a)
RETURN out_count, out_total, count(i) AS in_count, sum(i.amount) AS in_total"""
    rows = run_query(cypher, {"id": account_id})
    if not rows:
        return result(f"No account {account_id}.", {}, cypher)
    a = rows[0]["a"].properties
    paths = rows[0]["ps"] + rows[0]["qs"]
    g = paths_to_graph(paths)
    ids = lambda t: sorted({n["props"][KEY_PROP[t]] for n in g["nodes"] if n["type"] == t})
    money = run_query(money_cypher, {"id": account_id})[0]
    data = {**a, "account_age_days": age_days(a["opened_on"]), "upi_ids": ids("UPI"), "phones": ids("Phone"),
            "devices": ids("Device"), "ips": ids("IP"),
            "money_in": f"{money['in_count']} payments, {rupees(money['in_total'])}",
            "money_out": f"{money['out_count']} payments, {rupees(money['out_total'])}"}
    summary = (f"{a['holder']} ({account_id}), {a['bank']}, {a['city']}, account {data['account_age_days']} days old, "
               f"KYC {a['kyc']}, status {a['status']}. In: {data['money_in']}. Out: {data['money_out']}.")
    return result(summary, data, cypher + "\n\n" + money_cypher, paths)


# ----------------------------------------------------------------------------
# TOOL 3: who else uses the same phone / device / IP? (2-3 hops)
# ----------------------------------------------------------------------------
def find_shared_identifiers(account_id: str):
    """Find OTHER accounts that share a phone number, device or IP address with this account."""
    cypher = """
MATCH p = (a:Account {id: $id})-[:HAS_PHONE|USES_DEVICE]->(x)<-[:HAS_PHONE|USES_DEVICE]-(other:Account)
WHERE other <> a
RETURN p, other.id AS other, other.holder AS name, labels(x)[0] AS via, coalesce(x.number, x.id) AS value"""
    ip_cypher = """
MATCH p = (a:Account {id: $id})-[:USES_DEVICE]->(d1:Device)-[:SEEN_ON]->(ip:IP)<-[:SEEN_ON]-(d2:Device)<-[:USES_DEVICE]-(other:Account)
WHERE other <> a AND d1 <> d2
RETURN p, other.id AS other, other.holder AS name, 'IP' AS via, ip.addr AS value"""
    rows = run_query(cypher, {"id": account_id}) + run_query(ip_cypher, {"id": account_id})
    shared = {}
    for r in rows:
        shared.setdefault(f"{r['via']} {r['value']}", set()).add(f"{r['other']} ({r['name']})")
    data = {k: sorted(v) for k, v in shared.items()}
    others = {r["other"] for r in rows}
    if not rows:
        summary = f"{account_id} shares no phone, device or IP with any other account."
    else:
        summary = f"{account_id} shares identifiers with {len(others)} other accounts: " + "; ".join(
            f"{k} -> {len(v)} accounts" for k, v in data.items())
    return result(summary, {"shared": data, "other_accounts": sorted(others)}, cypher + "\n\n" + ip_cypher,
                  [r["p"] for r in rows])


# ----------------------------------------------------------------------------
# TOOL 4: follow the money (multi-hop, variable length path)
# ----------------------------------------------------------------------------
def trace_money_flow(account_id: str, direction: str = "out", max_hops: int = 4, max_gap_minutes: int = 120):
    """
    Follow money hop by hop. direction='out' = where did the money go next,
    'in' = where did it come from. Only keeps chains where each next payment
    happens soon after the previous one (rapid layering = typical mule behaviour).
    """
    hops = max(1, min(int(max_hops), 6))  # path length cannot be a $parameter in Cypher
    if direction == "in":
        pattern = f"(b:Account)-[:PAID*1..{hops}]->(a:Account {{id: $id}})"
    else:
        pattern = f"(a:Account {{id: $id}})-[:PAID*1..{hops}]->(b:Account)"
    cypher = f"""
MATCH p = {pattern}
WHERE all(i IN range(0, length(p) - 2) WHERE
          relationships(p)[i + 1].ts >= relationships(p)[i].ts AND
          relationships(p)[i + 1].ts - relationships(p)[i].ts <= $gap)
RETURN p, length(p) AS hops
ORDER BY hops DESC LIMIT 150"""
    rows = run_query(cypher, {"id": account_id, "gap": int(max_gap_minutes) * 60})
    paths = [r["p"] for r in rows]

    # Readable chains like: ACC1 -₹1,85,000-> ACC2 -₹1,81,000-> ACC3
    chains = []
    for p in paths[:6]:
        nodes = p.nodes()
        text = nodes[0].properties["id"]
        for e, n in zip(p.edges(), nodes[1:]):
            text += f" -{rupees(e.properties['amount'])} @{when(e.properties['ts'])}-> {n.properties['id']}"
        chains.append(text)
    reached = set()
    for p in paths:
        reached.update(n.properties["id"] for n in p.nodes())
    reached.discard(account_id)
    longest = rows[0]["hops"] if rows else 0
    summary = (f"Money traced {direction} from {account_id}: {len(paths)} rapid chains, longest {longest} hops, "
               f"{len(reached)} accounts involved.")
    return result(summary, {"accounts_involved": sorted(reached), "longest_chains": chains},
                  cypher, paths)


# ----------------------------------------------------------------------------
# TOOL 5: find the whole ring  (FalkorDB algorithm: Weakly Connected Components)
# ----------------------------------------------------------------------------
def find_fraud_ring(account_id: str):
    """
    Run FalkorDB's built-in algo.WCC (connected components) on the identity graph
    (accounts + phones + devices + IPs). Every account linked to this one through
    any chain of shared phones/devices/IPs ends up in the same component = the ring.
    """
    cypher = """
CALL algo.WCC({nodeLabels: ['Account', 'Phone', 'Device', 'IP'],
               relationshipTypes: ['HAS_PHONE', 'USES_DEVICE', 'SEEN_ON']})
YIELD node, componentId
WITH componentId, collect(node) AS members
WHERE any(n IN members WHERE n:Account AND n.id = $id)
RETURN [n IN members WHERE n:Account | n.id] AS accounts, size(members) AS component_size"""
    rows = run_query(cypher, {"id": account_id})
    accounts = sorted(rows[0]["accounts"]) if rows else [account_id]
    draw_cypher = """
MATCH p = (a:Account)-[:HAS_PHONE|USES_DEVICE]->(x)<-[:HAS_PHONE|USES_DEVICE]-(b:Account)
WHERE a.id IN $ids AND b.id IN $ids AND a <> b
RETURN p
UNION
MATCH p = (a:Account)-[:USES_DEVICE]->(:Device)-[:SEEN_ON]->(:IP)<-[:SEEN_ON]-(:Device)<-[:USES_DEVICE]-(b:Account)
WHERE a.id IN $ids AND b.id IN $ids AND a <> b
RETURN p"""
    paths = [r["p"] for r in run_query(draw_cypher, {"ids": accounts})] if len(accounts) > 1 else []
    details = run_query("MATCH (a:Account) WHERE a.id IN $ids RETURN a.id AS id, a.holder AS holder, "
                        "a.bank AS bank, a.opened_on AS opened_on, a.kyc AS kyc", {"ids": accounts})
    for d in details:
        d["account_age_days"] = age_days(d.pop("opened_on"))
    if len(accounts) == 1:
        summary = f"{account_id} is not linked to any other account by phone/device/IP (component of 1)."
    else:
        summary = f"algo.WCC found a connected identity ring of {len(accounts)} accounts around {account_id}."
    return result(summary, {"ring_accounts": details}, cypher, paths)


# ----------------------------------------------------------------------------
# TOOL 6: who is the boss? (FalkorDB algorithm: PageRank on the money graph)
# ----------------------------------------------------------------------------
def rank_ring_members(account_ids: list[str]):
    """
    Run algo.pageRank over all PAID edges and rank the given accounts.
    High PageRank = money keeps flowing INTO this account = likely collector / kingpin.
    Also shows money in vs out to spot pass-through mules.
    """
    cypher = """
CALL algo.pageRank('Account', 'PAID') YIELD node, score
WITH node, score WHERE node.id IN $ids
RETURN node.id AS id, node.holder AS holder, score
ORDER BY score DESC"""
    flow_cypher = """
MATCH (a:Account) WHERE a.id IN $ids
OPTIONAL MATCH ()-[i:PAID]->(a)
WITH a, sum(i.amount) AS money_in
OPTIONAL MATCH (a)-[o:PAID]->()
RETURN a.id AS id, money_in, sum(o.amount) AS money_out"""
    ranks = run_query(cypher, {"ids": account_ids})
    flows = {r["id"]: r for r in run_query(flow_cypher, {"ids": account_ids})}
    out = []
    for i, r in enumerate(ranks):
        f = flows.get(r["id"], {})
        m_in, m_out = f.get("money_in") or 0, f.get("money_out") or 0
        if i == 0:
            role = "top of the money flow (likely collector / kingpin)"
        elif m_in > 0 and 0.85 <= m_out / m_in <= 1.05:
            role = "pass-through mule (forwards almost everything)"
        elif m_in > 0 and m_out < 0.3 * m_in:
            role = "cash-out / sink account"
        else:
            role = "member"
        out.append({"id": r["id"], "holder": r["holder"], "pagerank": round(r["score"], 4),
                    "money_in": rupees(m_in), "money_out": rupees(m_out), "role": role})
    summary = f"PageRank ranking of {len(out)} accounts. Top: {out[0]['id']} ({out[0]['holder']})." if out else "No accounts."
    return result(summary, {"ranking": out}, cypher + "\n\n" + flow_cypher)


# ----------------------------------------------------------------------------
# TOOL 7: how are two accounts connected? (shortest path, any relationship)
# ----------------------------------------------------------------------------
def shortest_path_between(from_account: str, to_account: str):
    """Shortest connection between two accounts through ANY link (payments, phones, devices, IPs)."""
    cypher = """
MATCH (x:Account {id: $a}), (y:Account {id: $b})
RETURN shortestPath((x)-[*..10]-(y)) AS p"""
    rows = run_query(cypher, {"a": from_account, "b": to_account})
    p = rows[0]["p"] if rows else None
    if p is None:
        return result(f"No connection found between {from_account} and {to_account}.", {}, cypher)
    names = [node_info(n)["id"] for n in p.nodes()]
    steps = [names[0]]
    for e, n in zip(p.edges(), names[1:]):
        steps.append(f"-[{e.relation}]- {n}")
    summary = f"{from_account} and {to_account} are {len(p.edges())} hops apart: " + " ".join(steps)
    return result(summary, {"hops": len(p.edges()), "path": steps}, cypher, [p])


# ----------------------------------------------------------------------------
# TOOL 8: memory - have we investigated anything linked to this before?
# ----------------------------------------------------------------------------
def check_past_cases(account_id: str):
    """Look for earlier Case nodes (written by previous investigations) about this account or its phone/device/IP."""
    cypher = """
MATCH (c:Case)-[:ABOUT]->(a:Account {id: $id})
RETURN c.id AS case_id, c.action AS action, c.reason AS reason, 'this account' AS link
UNION
MATCH (a:Account {id: $id})-[:HAS_PHONE|USES_DEVICE]->(x)<-[:EVIDENCE]-(c:Case)
RETURN c.id AS case_id, c.action AS action, c.reason AS reason, labels(x)[0] + ' ' + coalesce(x.number, x.id) AS link
UNION
MATCH (a:Account {id: $id})-[:USES_DEVICE]->(:Device)-[:SEEN_ON]->(ip:IP)<-[:EVIDENCE]-(c:Case)
RETURN c.id AS case_id, c.action AS action, c.reason AS reason, 'IP ' + ip.addr AS link"""
    rows = run_query(cypher, {"id": account_id})
    if not rows:
        return result(f"No earlier cases linked to {account_id}.", {"cases": []}, cypher)
    summary = f"{len(rows)} earlier case link(s) for {account_id}: " + "; ".join(
        f"{r['case_id']} ({r['action']}) via {r['link']}" for r in rows)
    return result(summary, {"cases": rows}, cypher)


# ----------------------------------------------------------------------------
# TOOL 9: transparent risk score (rules on graph features, no black box)
# ----------------------------------------------------------------------------
# Each risk feature is one small, easy-to-read Cypher query.
RISK_FEATURES = {
    "device_sharers": "MATCH (a:Account {id: $id})-[:USES_DEVICE]->(:Device)<-[:USES_DEVICE]-(o:Account) "
                      "WHERE o <> a RETURN count(DISTINCT o) AS v",
    "phone_sharers": "MATCH (a:Account {id: $id})-[:HAS_PHONE]->(:Phone)<-[:HAS_PHONE]-(o:Account) "
                     "WHERE o <> a RETURN count(DISTINCT o) AS v",
    "ip_sharers": "MATCH (a:Account {id: $id})-[:USES_DEVICE]->(d1:Device)-[:SEEN_ON]->(:IP)<-[:SEEN_ON]-(d2:Device)"
                  "<-[:USES_DEVICE]-(o:Account) WHERE d1 <> d2 AND o <> a RETURN count(DISTINCT o) AS v",
    "senders": "MATCH (s:Account)-[:PAID]->(a:Account {id: $id}) RETURN count(DISTINCT s) AS v",
    "money_in": "MATCH ()-[i:PAID]->(a:Account {id: $id}) RETURN sum(i.amount) AS v",
    "money_out": "MATCH (a:Account {id: $id})-[o:PAID]->() RETURN sum(o.amount) AS v",
    "biggest_credit": "MATCH ()-[i:PAID]->(a:Account {id: $id}) RETURN max(i.amount) AS v",
    "in_times": "MATCH ()-[i:PAID]->(a:Account {id: $id}) RETURN collect(i.ts) AS v",
    "out_times": "MATCH (a:Account {id: $id})-[o:PAID]->() RETURN collect(o.ts) AS v",
    "money_from_new_accounts": "MATCH (s:Account)-[i:PAID]->(a:Account {id: $id}) WHERE s.opened_on > $cutoff "
                               "RETURN sum(i.amount) AS v",
    "new_account_senders": "MATCH (s:Account)-[i:PAID]->(a:Account {id: $id}) WHERE s.opened_on > $cutoff "
                           "RETURN count(DISTINCT s) AS v",
    "flagged_in": "MATCH ()-[i:PAID]->(a:Account {id: $id}) WHERE i.flagged = true RETURN count(i) AS v",
    "structured_out": "MATCH (a:Account {id: $id})-[o:PAID]->() WHERE o.amount >= 9000 AND o.amount < 10000 "
                      "RETURN count(o) AS v",
    "structured_in": "MATCH ()-[i:PAID]->(a:Account {id: $id}) WHERE i.amount >= 9000 AND i.amount < 10000 "
                     "RETURN count(i) AS v",
}


def compute_risk_score(account_ids: list[str]):
    """Score each account 0-100 from graph features, with the reasons written out."""
    scores = []
    for acc in account_ids:
        info = run_query("MATCH (a:Account {id: $id}) RETURN a.holder AS holder, a.opened_on AS opened_on, "
                         "a.kyc AS kyc", {"id": acc})
        if not info:
            continue
        params = {"id": acc, "cutoff": (date.today() - timedelta(days=120)).isoformat()}  # "new" = < 120 days old
        f = {name: run_query(q, params)[0]["v"] for name, q in RISK_FEATURES.items()}
        score, reasons = 0, []

        def add(points, why):
            nonlocal score
            score += points
            reasons.append(f"+{points} {why}")

        age = age_days(info[0]["opened_on"])
        m_in, m_out = f["money_in"] or 0, f["money_out"] or 0
        if age < 60:
            add(20, f"new account ({age} days old)")
        elif age < 180:
            add(8, f"fairly new account ({age} days)")
        if info[0]["kyc"] == "min":
            add(10, "minimum KYC only")
        if f["device_sharers"] >= 3:
            add(25, f"device shared with {f['device_sharers']} other accounts")
        elif f["device_sharers"] >= 1:
            add(10, f"device shared with {f['device_sharers']} other account(s)")
        if f["phone_sharers"] >= 2:
            add(20, f"phone number shared with {f['phone_sharers']} other accounts")
        elif f["phone_sharers"] == 1:
            add(10, "phone number shared with 1 other account")
        if f["ip_sharers"] >= 2:
            add(10, f"same IP address as {f['ip_sharers']} accounts on other devices")
        if m_in > 15000 and 0.85 <= m_out / m_in <= 1.05:
            add(15, f"pass-through: received {rupees(m_in)}, sent on {rupees(m_out)}")
        # rapid forwarding: money leaves within 60 minutes of arriving
        ins, outs = f["in_times"], f["out_times"]
        quick = sum(1 for t in ins if any(0 <= o - t <= 3600 for o in outs))
        if ins and quick / len(ins) >= 0.5 and (quick >= 2 or m_in >= 50000):
            add(20, f"forwards money fast ({quick} of {len(ins)} credits sent on within 1 hour)")
        if (f["biggest_credit"] or 0) >= 50000 and age < 120:
            add(15, f"large credit of {rupees(f['biggest_credit'])} into a {age}-day-old account")
        if f["structured_out"] >= 3:
            add(20, f"sent {f['structured_out']} payments just under ₹10,000 (structuring)")
        if f["structured_in"] >= 2 and age < 120:
            add(15, f"received {f['structured_in']} payments just under ₹10,000 (structuring)")
        if f["senders"] >= 5 and age < 90:
            add(10, f"{f['senders']} different senders into a new account (fan-in)")
        if f["new_account_senders"] >= 2 or (f["money_from_new_accounts"] or 0) >= 50000:
            add(15, f"collects {rupees(f['money_from_new_accounts'])} from {f['new_account_senders']} "
                    f"new account(s) (possible mule feeders)")
        if f["flagged_in"]:
            add(10, "received a flagged payment")
        score = min(score, 100)
        if score >= 60:
            rec = "FREEZE"
        elif score >= 40:
            rec = "ESCALATE"
        elif score >= 25:
            rec = "FLAG"
        else:
            rec = "CLEAR"
        scores.append({"id": acc, "holder": info[0]["holder"], "risk_score": score, "suggested": rec,
                       "reasons": reasons or ["no risk signals"]})
    scores.sort(key=lambda s: -s["risk_score"])
    high = [s["id"] for s in scores if s["risk_score"] >= 60]
    summary = (f"Scored {len(scores)} accounts. {len(high)} high risk (>=60). "
               + ", ".join(f"{s['id']}={s['risk_score']}" for s in scores[:12]))
    cypher = "\n".join(f"// {name}\n{q}" for name, q in RISK_FEATURES.items())
    return result(summary, {"scores": scores}, cypher)


# ----------------------------------------------------------------------------
# TOOL 10: ACT - write the decision back into the graph
# ----------------------------------------------------------------------------
def take_action(account_ids: list[str], action: str, reason: str, risk_score: int = 0, txn_id: str = ""):
    """
    Record the decision as a Case node in FalkorDB and update account status.
    action: FREEZE (block now), ESCALATE (send to a human analyst), FLAG (watch), CLEAR (no fraud).
    The Case links to the accounts (ABOUT) and to the shared phones/devices (EVIDENCE),
    so future investigations can find it (memory).
    """
    action = action.upper()
    status = {"FREEZE": "FROZEN", "ESCALATE": "UNDER_REVIEW", "FLAG": "FLAGGED"}.get(action)
    count = run_query("MATCH (c:Case) RETURN count(c) AS n")[0]["n"]
    case_id = f"CASE-{count + 1:04d}"
    cypher = """
CREATE (c:Case {id: $case_id, action: $action, reason: $reason, risk_score: $score,
                txn_id: $txn_id, created_at: $now})
WITH c
MATCH (a:Account) WHERE a.id IN $ids
CREATE (c)-[:ABOUT]->(a)
SET a.status = CASE WHEN $status IS NULL THEN a.status ELSE $status END
RETURN count(a) AS updated"""
    evidence_cypher = """
MATCH (c:Case {id: $case_id})
MATCH (a:Account)-[:HAS_PHONE|USES_DEVICE]->(x) WHERE a.id IN $ids
WITH c, x, count(DISTINCT a) AS k WHERE k > 1
MERGE (c)-[:EVIDENCE]->(x)
RETURN count(x) AS evidence"""
    params = {"case_id": case_id, "action": action, "reason": reason, "score": int(risk_score),
              "txn_id": txn_id, "now": int(time.time()), "ids": account_ids, "status": status}
    updated = run_query(cypher, params)[0]["updated"]
    evidence = run_query(evidence_cypher, params)[0]["evidence"] if action != "CLEAR" else 0
    paths = [r["p"] for r in run_query("MATCH p = (c:Case {id: $case_id})-[:ABOUT|EVIDENCE]->() RETURN p",
                                       {"case_id": case_id})]
    summary = (f"{case_id} written to FalkorDB: {action} on {updated} account(s)"
               f"{', status set to ' + status if status else ''}; {evidence} shared phone/device nodes linked as evidence.")
    return result(summary, {"case_id": case_id, "action": action, "accounts": account_ids, "risk_score": risk_score,
                            "reason": reason}, cypher + "\n\n" + evidence_cypher, paths)


# ----------------------------------------------------------------------------
# The tool list the LLM sees. "parameters" is standard JSON Schema.
# ----------------------------------------------------------------------------
def _schema(props, required):
    return {"type": "object", "properties": props, "required": required}


ID = {"type": "string", "description": "Account id, e.g. ACC1234"}
IDS = {"type": "array", "items": {"type": "string"}, "description": "List of account ids"}

TOOLS = [
    {"name": "get_transaction", "function": get_transaction,
     "description": "Look up one transaction: payer, payee, amount, time, flag reason. Start here.",
     "parameters": _schema({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_account_profile", "function": get_account_profile,
     "description": "Profile of an account: age, KYC, UPI IDs, phones, devices, IPs, money in/out.",
     "parameters": _schema({"account_id": ID}, ["account_id"])},
    {"name": "find_shared_identifiers", "function": find_shared_identifiers,
     "description": "Find other accounts that share a phone number, device or IP with this account (multi-hop).",
     "parameters": _schema({"account_id": ID}, ["account_id"])},
    {"name": "trace_money_flow", "function": trace_money_flow,
     "description": "Follow the money several hops in or out of an account, keeping only rapid chains (layering).",
     "parameters": _schema({"account_id": ID,
                            "direction": {"type": "string", "enum": ["in", "out"]},
                            "max_hops": {"type": "integer", "description": "1-6, default 4"},
                            "max_gap_minutes": {"type": "integer", "description": "max minutes between hops, default 120"}},
                           ["account_id", "direction"])},
    {"name": "find_fraud_ring", "function": find_fraud_ring,
     "description": "Graph algorithm (connected components, algo.WCC): returns every account linked to this one "
                    "through shared phones/devices/IPs = the whole ring.",
     "parameters": _schema({"account_id": ID}, ["account_id"])},
    {"name": "rank_ring_members", "function": rank_ring_members,
     "description": "Graph algorithm (algo.pageRank on payments): ranks accounts to find the collector/kingpin and pass-through mules.",
     "parameters": _schema({"account_ids": IDS}, ["account_ids"])},
    {"name": "shortest_path_between", "function": shortest_path_between,
     "description": "Shortest connection between two accounts through any link (payments, phones, devices, IPs).",
     "parameters": _schema({"from_account": ID, "to_account": ID}, ["from_account", "to_account"])},
    {"name": "check_past_cases", "function": check_past_cases,
     "description": "Memory: earlier investigation cases linked to this account or its phone/device/IP.",
     "parameters": _schema({"account_id": ID}, ["account_id"])},
    {"name": "compute_risk_score", "function": compute_risk_score,
     "description": "Transparent 0-100 risk score per account with written reasons and a suggested action.",
     "parameters": _schema({"account_ids": IDS}, ["account_ids"])},
    {"name": "take_action", "function": take_action,
     "description": "Final step. Write the decision into the graph as a Case: FREEZE, ESCALATE (human review), "
                    "FLAG (watch) or CLEAR (not fraud). Call once per group of accounts that get the same action.",
     "parameters": _schema({"account_ids": IDS,
                            "action": {"type": "string", "enum": ["FREEZE", "ESCALATE", "FLAG", "CLEAR"]},
                            "reason": {"type": "string", "description": "short explanation citing the graph evidence"},
                            "risk_score": {"type": "integer"},
                            "txn_id": {"type": "string", "description": "the flagged transaction being investigated"}},
                           ["account_ids", "action", "reason"])},
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def run_tool(name, args):
    """Run a tool by name with a dict of arguments (this is what the agent loop calls)."""
    if name not in TOOLS_BY_NAME:
        return result(f"Unknown tool {name}", {}, "")
    try:
        return TOOLS_BY_NAME[name]["function"](**args)
    except Exception as e:  # tell the LLM what went wrong so it can try again
        return result(f"Tool {name} failed: {e}", {"error": str(e)}, "")
