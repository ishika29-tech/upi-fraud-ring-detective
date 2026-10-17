# LEARN.md: how this agent works, explained with our own code

Read this top to bottom once and you will be able to explain every part to the judges.

---

## 1. What is an "AI agent"?

A normal chatbot only **talks**. An agent can also **do things**: it calls tools, looks at the results, and decides what to do next.

In our project the whole agent is one loop in `agent.py` (`run_gemini` / `run_openai`):

```
task = "Investigate flagged transaction TXN502640"
repeat (max 12 times):
    ask the LLM: here is the task, the history so far, and the list of tools
    if the LLM says "call tool X with arguments Y":
        run X(Y) on FalkorDB, then add the result to the history
    else:
        the LLM wrote its final answer -> stop
```

That's it. The "intelligence" is the LLM choosing **which tool next**, based on what it has seen so far.
For example, it sees that the account shares a device with 7 others, so it decides to call `find_fraud_ring`.

> 💡 Compare with `run_offline()` in `agent.py`: same tools, but the order is hard-coded with if/else.
> The LLM version adapts. For example, it also profiles the payer when something looks odd, and it decides how to split FREEZE and ESCALATE.

## 2. What is a "tool"?

A tool is just a Python function plus a description the LLM can read. Look at `TOOLS` at the bottom of `tools.py`:

```python
{"name": "find_fraud_ring",
 "function": find_fraud_ring,
 "description": "Graph algorithm (connected components, algo.WCC): returns every account linked ...",
 "parameters": {"type": "object", "properties": {"account_id": {...}}, "required": ["account_id"]}}
```

- `description`: the LLM reads this to decide **when** to use the tool.
- `parameters`: a JSON Schema telling the LLM **what arguments** to send.
- We send this list to Gemini as `FunctionDeclaration`s. Gemini answers with a `function_call` like
  `find_fraud_ring(account_id="ACC46758")`, and `run_tool()` runs it.

**Important design choice:** the LLM never writes Cypher itself. It can only pick from our safe, tested tools.
That makes the agent reliable and explainable, and it can't delete the database by accident.

## 3. How a tool becomes a Cypher query

Example: `find_shared_identifiers` in `tools.py`:

```cypher
MATCH p = (a:Account {id: $id})-[:HAS_PHONE|USES_DEVICE]->(x)<-[:HAS_PHONE|USES_DEVICE]-(other:Account)
WHERE other <> a
RETURN p, other.id AS other, labels(x)[0] AS via
```

Read it like a picture: *start at account `a`, go out along a HAS_PHONE or USES_DEVICE edge to some thing `x`,
then come back in from `x` to a **different** account `other`.* Whatever `x` is (a phone or a device), it is shared.
`$id` is a parameter, so we never paste user text into the query (safe from injection).

Every tool returns a dict with `summary`, `data`, `cypher` and `graph`. The LLM gets the summary and data (`for_llm()`),
and the UI uses `cypher` (the "Why?" panel) and `graph` (the drawing).

## 4. What does "multi-hop" mean?

A **hop** = following one edge. "Multi-hop reasoning" = the answer needs several hops:

```
Victim -PAID-> ACC65658 -PAID-> ACC98257 -PAID-> ACC33288 -PAID-> ACC77159 -PAID-> ACC34618
           hop 1          hop 2         hop 3         hop 4          hop 5
```

`trace_money_flow` does this in ONE query with a variable-length path: `-[:PAID*1..4]->` means "1 to 4 PAID edges in a row".
We also keep only chains where each payment happens within 2 hours of the previous one, because fast hops = layering.

There are two kinds of multi-hop in this project:
1. **Inside one query**: variable-length paths, Account → Device → IP → Device → Account.
2. **Across the agent's steps**: transaction → receiver → shared device → ring → money trail → kingpin → action.
   Each step uses what the previous step found.

## 5. The graph algorithms (FalkorDB built-ins)

| Algorithm | Plain-language meaning | Our tool |
|---|---|---|
| `algo.WCC` (Weakly Connected Components) | "Split the graph into islands. Everything you can reach is on your island." We run it only on Account/Phone/Device/IP edges, so each island is a group of accounts linked by shared identity = a ring. | `find_fraud_ring` |
| `algo.pageRank` | "A node is important if money flows into it from other nodes that receive money." The top one is usually the collector. | `rank_ring_members` |
| `shortestPath` | "Fewest hops between A and B through any edges." | `shortest_path_between` |

Try one yourself in FalkorDB Browser (http://localhost:3000):

```cypher
CALL algo.pageRank('Account', 'PAID') YIELD node, score
RETURN node.holder, score ORDER BY score DESC LIMIT 5
```

## 6. What is MCP?

**Model Context Protocol** = a standard plug, like USB, between AI apps and tools.
Without MCP, every AI app needs custom code for our tools. With MCP, we describe the tools once (`mcp_server.py`)
and any MCP app (Claude Desktop, Cursor, VS Code, our own `mcp_agent.py`) can:
1. ask the server `list_tools`, which returns names, descriptions and argument schemas,
2. let its LLM **choose** a tool,
3. call it with `call_tool`.

In `mcp_server.py` each tool is one small function with `@mcp.tool()` above it. FastMCP reads the type hints and docstring to build the schema.

## 7. Memory

`take_action` writes a `Case` node into the graph, linked to the accounts (`ABOUT`) and to the shared phones/devices (`EVIDENCE`).
Next time anything touches that device, `check_past_cases` finds it, even in a new session or after a restart, because it lives in the database, not in the LLM.

## 8. Explainability

- Every step shows its exact Cypher (UI → "Why?" panel).
- The risk score is a list of human-readable reasons ("+20 forwards money fast (2 of 2 credits sent on within 1 hour)").
- The final answer must contain `VERDICT / RING / PATH / WHY` (we ask for this in `SYSTEM_PROMPT`).

## 9. Judge Q&A cheat sheet

- **Why a graph and not SQL?** Rings are about connections that are 3–6 hops deep. In SQL that means many self-joins.
  In Cypher it is one pattern (`PAID*1..4`), and FalkorDB is fast enough to run one on every step of the agent loop.
- **Why doesn't the LLM write Cypher itself?** Safety and reliability. Tools are tested, parameterised and explainable.
  The LLM's job is reasoning: which tool next, and what the evidence means.
- **How do you avoid freezing innocent people?** Scores need several independent signals. We planted a family that shares a tablet,
  and the agent CLEARs them (old accounts, full KYC, no rapid forwarding). See `evaluate.py`: 0 false alarms.
- **Where do actions go?** Into FalkorDB as `Case` nodes + account status. A bank system would read those.
- **What if the AI API is down?** Fallback models, and an offline rule-based mode with the same tools.
- **Is the data real?** Synthetic, generated by `generate_data.py` with patterns from public mule-account typologies (fan-in, layering, structuring under ₹10,000, shared devices).

## 10. Mini glossary

- **Mule account**: an account (often rented or opened with someone's documents) used to move stolen money.
- **Layering**: moving money quickly through many accounts to hide where it came from.
- **Structuring**: splitting money into amounts just under a reporting or alert threshold (here ₹10,000).
- **VPA / UPI ID**: `name@bank` address used to receive UPI payments.
- **Cypher**: the query language for graphs. `MATCH` a pattern, `RETURN` what you want.
