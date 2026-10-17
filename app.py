"""
app.py - Streamlit demo UI.

Run:  streamlit run app.py

Pick a flagged transaction -> press Investigate -> watch the agent call graph tools
step by step while the graph grows on the right -> read the verdict + the "why".
"""
import json
import os
import subprocess
import sys

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

import agent
import tools
from db import run_query

st.set_page_config(page_title="UPI Fraud Ring Detective", page_icon="🕵️", layout="wide")

# ----------------------------------------------------------------------------
# Graph drawing (pyvis = vis.js network inside an HTML string)
# ----------------------------------------------------------------------------
STYLE = {  # node type -> (shape, colour, size)
    "Account": ("dot", "#4C9BE8", 18),
    "UPI": ("diamond", "#B07CFF", 9),
    "Phone": ("triangle", "#FFA94D", 12),
    "Device": ("square", "#51CF66", 12),
    "IP": ("dot", "#ADB5BD", 9),
    "Case": ("star", "#FFFFFF", 22),
}
ACTION_COLOUR = {"FREEZE": "#FF4B4B", "ESCALATE": "#FF922B", "FLAG": "#FCC419", "CLEAR": "#40C057"}


def empty_state():
    return {"nodes": {}, "edges": {}, "fresh_edges": set(), "start": None, "ring": set(), "actions": {}}


def add_to_state(state, graph):
    """Merge one tool result's nodes/edges into what is already drawn."""
    state["fresh_edges"] = set()
    for n in graph["nodes"]:
        state["nodes"][n["id"]] = n
    for e in graph["edges"]:
        key = (e["from"], e["to"], e["type"], e["props"].get("txn_id", ""))
        state["edges"][key] = e
        state["fresh_edges"].add(key)


def draw(state, height=680):
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#0E1117", font_color="#E9ECEF",
                  cdn_resources="remote")
    for node_id, n in state["nodes"].items():
        shape, colour, size = STYLE.get(n["type"], ("dot", "#888", 10))
        if n["type"] == "Account":
            acc = n["props"]["id"]
            if n["props"].get("is_merchant"):
                colour = "#20C997"
            if acc in state["ring"]:
                colour, size = "#FF6B6B", 22
            if acc in state["actions"]:
                colour, size = ACTION_COLOUR[state["actions"][acc]], 24
            if acc == state["start"]:
                size = 30
        title = "\n".join(f"{k}: {v}" for k, v in n["props"].items())
        net.add_node(node_id, label=n["caption"], shape=shape, color=colour, size=size, title=title,
                     font={"size": 16 if n["type"] == "Account" else 12, "color": "#E9ECEF"},
                     borderWidth=4 if n["type"] == "Account" and n["props"]["id"] == state["start"] else 1)
    for key, e in state["edges"].items():
        fresh = key in state["fresh_edges"]
        label = tools.rupees(e["props"]["amount"]) if e["type"] == "PAID" else e["type"].lower()
        colour = "#FFD43B" if fresh else ("#868E96" if e["type"] == "PAID" else "#495057")
        net.add_edge(e["from"], e["to"], label=label, color=colour, width=4 if fresh else 1.5,
                     font={"size": 12, "color": "#CED4DA", "strokeWidth": 0})
    net.set_options(json.dumps({
        "physics": {"barnesHut": {"gravitationalConstant": -2500, "springLength": 70, "avoidOverlap": 0.3},
                    "stabilization": {"iterations": 150}},
        "interaction": {"hover": True, "tooltipDelay": 100},
        "edges": {"arrows": {"to": {"enabled": True, "scaleFactor": 0.5}}, "smooth": {"type": "dynamic"}},
    }))
    return net.generate_html()


# ----------------------------------------------------------------------------
# Rendering one agent event (used live AND when re-showing a finished run)
# ----------------------------------------------------------------------------
TOOL_ICON = {"get_transaction": "🧾", "get_account_profile": "🪪", "find_shared_identifiers": "🔗",
             "trace_money_flow": "💸", "find_fraud_ring": "🕸️", "rank_ring_members": "👑",
             "shortest_path_between": "🧭", "check_past_cases": "🗂️", "compute_risk_score": "📊",
             "take_action": "🚨"}


def apply_event(state, event):
    """Update the graph state from an event."""
    if event["type"] != "tool_result":
        return
    res = event["result"]
    add_to_state(state, res["graph"])
    if event["tool"] == "get_transaction" and res["data"]:
        state["start"] = res["data"]["to_id"]
    if event["tool"] == "find_fraud_ring":
        accounts = [a["id"] for a in res["data"].get("ring_accounts", [])]
        if len(accounts) > 1:
            state["ring"].update(accounts)
    if event["tool"] == "take_action" and res["data"].get("accounts"):
        for acc in res["data"]["accounts"]:
            state["actions"][acc] = res["data"]["action"]


def show_event(container, event, step_no):
    if event["type"] == "tool_result":
        res = event["result"]
        args = ", ".join(f"{k}={v}" for k, v in event["args"].items())
        with container.expander(f"{TOOL_ICON.get(event['tool'], '🔧')} Step {step_no}: **{event['tool']}**"
                                f"({args[:80]})", expanded=event["tool"] == "take_action"):
            st.write(res["summary"])
            if res["cypher"]:
                st.code(res["cypher"], language="cypher")
            st.json(res["data"], expanded=False)
    elif event["type"] == "info":
        container.caption("ℹ️ " + event["text"])
    elif event["type"] == "error":
        container.error(event["text"] + "\n\nTip: switch the provider to 'offline' in the sidebar to run without an API key.")


def show_verdict(events):
    actions = [e["result"]["data"] for e in events if e["type"] == "tool_result" and e["tool"] == "take_action"
               and e["result"]["data"].get("case_id")]
    final = next((e["text"] for e in events if e["type"] == "final"), "")
    if actions:
        cols = st.columns(len(actions))
        for col, a in zip(cols, actions):
            colour = ACTION_COLOUR.get(a["action"], "#888")
            col.markdown(
                f"<div style='border:2px solid {colour};border-radius:12px;padding:14px'>"
                f"<div style='font-size:28px;font-weight:800;color:{colour}'>{a['action']}</div>"
                f"<div>{len(a['accounts'])} account(s) · risk {a['risk_score']} · {a['case_id']} saved in FalkorDB</div>"
                f"<div style='font-size:13px;opacity:.8'>{', '.join(a['accounts'])}</div></div>",
                unsafe_allow_html=True)
    if final:
        st.markdown("#### 🧠 Agent's explanation")
        st.text(final)
    queries = [e for e in events if e["type"] == "tool_result" and e["result"]["cypher"]]
    with st.expander(f"🔎 Why? Every Cypher query the agent ran ({len(queries)})"):
        for i, e in enumerate(queries, 1):
            st.markdown(f"**{i}. {e['tool']}** → {e['result']['summary']}")
            st.code(e["result"]["cypher"], language="cypher")


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
st.sidebar.title("🕵️ UPI Fraud Ring Detective")
st.sidebar.caption("An AI agent that investigates UPI fraud by walking a FalkorDB graph.")
providers = ["gemini", "openai", "offline"]
default = os.getenv("LLM_PROVIDER", "gemini")
provider = st.sidebar.selectbox("LLM provider", providers, index=providers.index(default) if default in providers else 0,
                                help="offline = no API key needed; the same tools run in a fixed order")
try:
    stats = run_query("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC")
    edges = run_query("MATCH ()-[r:PAID]->() RETURN count(r) AS n")[0]["n"]
    st.sidebar.success("Connected to FalkorDB")
    st.sidebar.markdown("  \n".join(f"**{s['n']}** {s['label']} nodes" for s in stats)
                        + f"  \n**{edges}** PAID edges (UPI payments)")
except Exception as e:
    st.sidebar.error(f"Cannot reach FalkorDB: {e}\n\nStart it with `docker compose up -d`, then run `python generate_data.py`.")
    st.stop()
if st.sidebar.button("♻️ Reset demo data"):
    with st.spinner("Re-generating the graph..."):
        subprocess.run([sys.executable, "generate_data.py"], check=True)
    st.session_state.pop("events", None)
    st.rerun()

# ----------------------------------------------------------------------------
# Main page
# ----------------------------------------------------------------------------
tab_investigate, tab_cases, tab_how = st.tabs(["🔍 Investigate", "🗂️ Cases (agent memory)", "ℹ️ How it works"])

with tab_investigate:
    st.markdown("### Start from ONE flagged UPI payment. The agent finds the whole ring.")
    st.caption("India saw 12.64 lakh UPI fraud cases worth ₹981 crore in FY 2024-25 (Ministry of Finance, Lok Sabha). "
               "Stolen money moves through rings of mule accounts that share phones, devices and IPs - "
               "invisible in a table, obvious in a graph.")
    flagged = tools.list_flagged_transactions()["data"]
    options = {f"{f['txn_id']} · {f['amount_text']} · {f['from_name']} → {f['to_name']} · {f['reason']}": f["txn_id"]
               for f in flagged}
    pick = st.selectbox("Flagged transaction", list(options), index=0 if options else None)
    c1, c2 = st.columns([3, 1])
    typed = c1.text_input("...or type any transaction id", placeholder="TXN5xxxxx")
    go = c2.button("🔍 Investigate", type="primary", use_container_width=True)
    txn_id = typed.strip() or (options[pick] if pick else "")

    left, right = st.columns([2, 3])
    steps_box = left.container(height=680)
    graph_box = right.empty()

    if go and txn_id:
        events, state, step_no = [], empty_state(), 0
        with st.spinner(f"Agent is investigating {txn_id}..."):
            for event in agent.investigate(txn_id, provider):
                events.append(event)
                apply_event(state, event)
                if event["type"] == "tool_result":
                    step_no += 1
                    with graph_box:
                        components.html(draw(state), height=690)
                show_event(steps_box, event, step_no)
        st.session_state["events"] = events
        st.session_state["txn"] = txn_id
        show_verdict(events)
    elif st.session_state.get("events"):  # show the last finished investigation again
        events, state, step_no = st.session_state["events"], empty_state(), 0
        for event in events:
            apply_event(state, event)
            if event["type"] == "tool_result":
                step_no += 1
            show_event(steps_box, event, step_no)
        state["fresh_edges"] = set()
        with graph_box:
            components.html(draw(state), height=690)
        show_verdict(events)
    else:
        steps_box.info("Pick a flagged transaction and press **Investigate**. Each tool call the agent makes will appear here.")
        graph_box.markdown("**Legend** - 🔵 account · 🟣 UPI ID · 🟠 phone · 🟩 device · ⚪ IP · ⭐ case<br>"
                           "🔴 frozen · 🟧 escalated · 🟨 flagged · 🟢 cleared · yellow edges = just explored",
                           unsafe_allow_html=True)

with tab_cases:
    st.markdown("Every decision is written back into FalkorDB as a `Case` node. Later investigations read them "
                "with `check_past_cases`, so the agent **remembers across sessions**.")
    cases = run_query("MATCH (c:Case) OPTIONAL MATCH (c)-[:ABOUT]->(a:Account) "
                      "RETURN c.id AS case_id, c.action AS action, c.risk_score AS risk, c.txn_id AS txn, "
                      "collect(a.id) AS accounts, c.reason AS reason ORDER BY case_id DESC")
    if cases:
        st.dataframe(cases, use_container_width=True)
    else:
        st.info("No cases yet. Run an investigation first.")

with tab_how:
    st.markdown("""
**Graph (FalkorDB):** `Account` -HAS_UPI-> `UPI`, -HAS_PHONE-> `Phone`, -USES_DEVICE-> `Device` -SEEN_ON-> `IP`,
`Account` -PAID {amount, ts}-> `Account`, and `Case` nodes written by the agent.

**Agent loop:** the LLM gets the flagged transaction and a list of tools. It picks a tool, we run the
Cypher on FalkorDB, send back the result, and repeat until it calls `take_action` and explains itself.

**Graph algorithms used as tools:** `algo.WCC` (connected components → the ring), `algo.pageRank`
(who collects the money), `shortestPath` (how two accounts connect), variable-length paths `PAID*1..k` (money trail).

**MCP:** the same tools are served by `mcp_server.py`, so Claude Desktop / Cursor can pick and call them.
""")
