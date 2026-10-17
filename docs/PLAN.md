# Design plan — UPI Fraud Ring Detective

Written before the hackathon start (2026-10-14 ~17:45 UTC, i.e. before 12:01 AM IST Oct 15).
This file is only a plan. All project code is written after the event starts.

## Problem
UPI fraud in India: 12.64 lakh cases and ₹981 crore lost in FY 2024-25 (Ministry of Finance reply in Lok Sabha, reported by BOOM).
Stolen money is moved through *mule accounts*: many accounts, opened by different people, but controlled by the same gang —
same phones, same devices, same IPs, and money that hops quickly from account to account.
Looking at one transaction in a table tells you nothing. Looking at the *graph* around it shows the whole ring.

## One-line pitch
Give the agent ONE flagged ₹ transaction. It walks the FalkorDB graph hop by hop (account → UPI → phone → device → IP → other accounts → money flow),
uses graph algorithms as tools, finds the whole mule ring, writes a FREEZE / FLAG / ESCALATE case back into the graph,
and shows the exact path + Cypher behind every decision.

## Graph schema (FalkorDB)
Nodes
- `Account {id, holder, bank, city, opened_on, kyc, status, is_merchant}`
- `UPI {vpa}`                      e.g. `rohan.sharma@okaxis`
- `Phone {number}`
- `Device {id, model}`
- `IP {addr, isp, city}`
- `Case {id, action, risk_score, summary, created_at}`   (written by the agent = its memory)

Relationships
- `(Account)-[:HAS_UPI]->(UPI)`
- `(Account)-[:HAS_PHONE]->(Phone)`
- `(Account)-[:USES_DEVICE]->(Device)`
- `(Device)-[:SEEN_ON]->(IP)`
- `(Account)-[:PAID {txn_id, amount, ts, flagged}]->(Account)`   money moves along these
- `(Case)-[:ABOUT]->(Account)`, `(Case)-[:EVIDENCE]->(Device|Phone|IP)`

## Synthetic data (generate_data.py)
~400 normal accounts, ~3,000 normal payments (rent, groceries, friends, merchants), Indian names / banks / cities / VPAs.
Planted patterns (the "answer key", also saved to a JSON file so tests can check the agent):
1. Shared-device mule farm — 7 accounts at different banks, all on 2 devices behind 1 IP; victims pay in (fan-in).
2. Layering chain — victim → A → B → C → D → cash-out within ~20 minutes, each hop keeps a small cut.
3. Fan-out / structuring — one collector splits ₹1L+ into many payments just under ₹10,000.
4. One phone, many UPI IDs — 5 VPAs registered to the same phone number.
5. Fake merchant — "merchant" account collecting from many victims and forwarding everything to a mule.
False-positive trap: a normal family (3 accounts) sharing one tablet. The agent should NOT freeze them.

## Agent tools (tools.py) — each tool = one Cypher query or one FalkorDB algorithm
Every tool returns: the result, the exact Cypher it ran, and the nodes/edges it touched (for the UI).
- `get_transaction(txn_id)`              — who paid whom, amount, time
- `get_account_profile(account_id)`      — account + its UPI IDs, phones, devices, IPs (1 hop)
- `find_shared_identifiers(account_id)`  — other accounts sharing a phone/device/IP (2–3 hops)
- `trace_money_flow(account_id, direction, max_hops)` — variable-length `PAID*1..k` paths (multi-hop)
- `find_fraud_ring(account_id)`          — `algo.WCC` over identity links (device/phone/IP) → the whole cluster
- `rank_ring_members(account_ids)`       — `algo.pageRank` on money flow → who is the collector/kingpin
- `shortest_path_between(a, b)`          — `algo.SPpaths` → "how is X connected to Y?"
- `check_past_cases(account_id)`         — reads earlier `Case` nodes (memory across sessions)
- `compute_risk_score(account_ids)`      — transparent rule score from graph features (+ reasons)
- `take_action(account_ids, action, reason)` — writes a `Case` node + sets account status (FROZEN / FLAGGED / REVIEW)

## Agent loop (agent.py)
Plain while-loop: send question + tool list to the LLM → LLM picks tool(s) → run Cypher → send result back → repeat (max ~12 steps) → final verdict.
Providers: Gemini (default, `google-genai`), OpenAI (optional), and an offline "scripted" mode that runs the same tools in a fixed order
(so the demo works even if the free API quota runs out — free Gemini keys have small daily limits).
Each step is yielded as an event so the UI can show it live.

## MCP (mcp_server.py, optional)
The same tool functions exposed with FastMCP, so Claude Desktop / Cursor / any MCP client can choose and call them.

## UI (app.py, Streamlit)
Pick a flagged transaction → "Investigate" → live step list (tool, why, Cypher, result) + graph that grows hop by hop (pyvis)
→ ring lights up red, path highlighted → verdict card (action, risk score, reasons) → "Why?" panel with path + Cypher.

## Run
`docker compose up -d` (FalkorDB) → `pip install -r requirements.txt` → `python generate_data.py` → `streamlit run app.py`.
Deploy: Streamlit Community Cloud + FalkorDB Cloud free instance.

## Verified on the build box before the event (environment checks only)
- FalkorDB docker image runs; Python client `falkordb` connects.
- Built-in procedures available: algo.WCC, algo.labelPropagation, algo.pageRank, algo.betweenness, algo.BFS, algo.SPpaths, algo.SSpaths, algo.MSF.
- `falkordblite` (embedded FalkorDB, no Docker) works on Python 3.12+.
- Gemini via google-genai supports function calling; free-tier daily request limits are small → design keeps LLM calls few (parallel tool calls) and has a fallback model + offline mode.
