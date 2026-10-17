# I gave an AI agent one ₹18,000 UPI payment. It found an 8-account mule ring.

*Our Graph Hacks project: an agent that investigates UPI fraud by walking a FalkorDB graph.*

## The problem with looking at one transaction

UPI is how India pays, and fraud follows the money: **12.64 lakh UPI fraud cases worth ₹981 crore in FY 2024-25**
(Ministry of Finance in the Lok Sabha). The money rarely stays in the first account. It moves through **mule accounts**:
new accounts, often with minimum KYC, run by the same gang from the same phones, devices and IP addresses.

A fraud analyst who looks at one flagged row sees "₹18,000, Aarav → Kavya". The interesting part is everything *around* that row,
and that's a graph.

## The idea

We built **UPI Fraud Ring Detective** for Track 1 of Graph Hacks (*Agents That Act on Connected Data*). You give it one flagged transaction.
An LLM agent (Gemini) investigates by calling **graph tools** on **FalkorDB**, finds the whole ring, takes an action, and explains itself.

## Modelling UPI as a graph

```
(Account)-[:HAS_UPI]->(UPI)        rohan.sharma@okaxis
(Account)-[:HAS_PHONE]->(Phone)
(Account)-[:USES_DEVICE]->(Device)-[:SEEN_ON]->(IP)
(Account)-[:PAID {amount, ts}]->(Account)
```

We generated about 430 accounts and 2,700 payments with Indian names, banks, cities and UPI handles. Then we hid 5 fraud patterns inside:
a device farm, a 5-hop layering chain, structuring just under ₹10,000, one phone with many UPI IDs, and a fake merchant.
We also planted **traps**: a family that shares a tablet, and a big rent payment. A good investigator should *not* freeze those.

## Tools, not free-form queries

We didn't let the LLM write Cypher. Instead we gave it 11 tools, each one a tested Cypher query or a FalkorDB algorithm:

- `find_shared_identifiers`: Account → Device → IP → Device → Account, in one pattern
- `trace_money_flow`: variable-length paths `-[:PAID*1..4]->` that keep only chains where each hop is under 2 hours
- `find_fraud_ring`: **`algo.WCC`** on the identity sub-graph. Each connected island = one ring
- `rank_ring_members`: **`algo.pageRank`** on payments to find the collector
- `take_action`: writes a `Case` node back into the graph and freezes or escalates accounts

The LLM's job is reasoning: *which tool next, and what does this evidence mean?*

## What it looks like

*(screenshot: steps on the left, the graph growing hop by hop on the right, the ring turning red)*

On the "fake courier" complaint the agent made 12 tool calls. It found the receiver shares a device and IP with 7 other new accounts,
ran connected components (8 accounts), traced the money to a collector, confirmed the collector with PageRank, froze all 8,
and explained the exact path. On the family-tablet trap it said **CLEAR**.

Against our answer key it caught **27 of 28 ring accounts with 0 false alarms**.

## MCP: the same tools everywhere

All tools are also an **MCP server**. Claude Desktop or Cursor can list them and use our fraud graph without any custom code,
and our `mcp_agent.py` discovers its tools over MCP instead of importing them.

## What we learned

- *(write your own: e.g. first time using Cypher, how variable-length paths work, why the agent needed a time-gap rule...)*
- Graph algorithms make great agent tools, because one call answers a question that would take an LLM many steps.
- Explainability comes almost for free when every tool returns the query it ran.

## Try it

GitHub: *(your repo link)* · Live demo: *(your Streamlit link)* · Video: *(your YouTube link)*

Built by *(names)*, Punjab Engineering College, for Graph Hacks by WeMakeDevs × FalkorDB.
