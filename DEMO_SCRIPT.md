# DEMO_SCRIPT.md: 2-minute demo video

**Setup before recording:** app open on the Investigate tab, sidebar provider = gemini (or offline), and you have pressed "♻️ Reset demo data" so the Cases tab is empty. FalkorDB Browser is open in a second tab.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:15 | App home | "India had 12.6 lakh UPI fraud cases last year. The stolen money moves through rings of mule accounts. One flagged payment looks harmless in a table, so we built an agent that investigates it in a graph." |
| 0:15–0:25 | Pick `TXN502640 · ₹18,000 · fake courier` | "A victim reported this ₹18,000 payment to 1930. Let's give our agent just this one transaction." Click **Investigate**. |
| 0:25–1:05 | Steps appear left, graph grows right | "The agent, powered by Gemini, chooses its own tools. It profiles the receiver: 25 days old, minimum KYC. It finds the same device and IP used by 7 other accounts. Now it runs FalkorDB's connected-components algorithm: there's the whole ring, 8 accounts. It follows the money: every account forwards within minutes to one collector. PageRank confirms the collector is the kingpin." Open one step and show its Cypher. |
| 1:05–1:25 | Verdict card turns red | "Then it acts. It writes a FREEZE case into FalkorDB and freezes all 8 accounts. The victim stays untouched." Scroll to the explanation: "Every decision has a path and a reason." Open **Why?** to show all the Cypher. |
| 1:25–1:40 | Pick the family transaction `₹52,000 ... shared device` → Investigate | "A naive rule would flag this family because they share a tablet. Our agent checks the graph: old accounts, full KYC, no fast forwarding. CLEAR. No innocent family frozen." |
| 1:40–1:50 | Cases tab | "Every decision is stored in the graph as memory. The next investigation that touches that device sees the old case." |
| 1:50–2:00 | Terminal: `python mcp_agent.py TXN502718` (or Claude Desktop) | "And all 11 tools are also an MCP server, so Claude Desktop or Cursor can use our fraud graph too. UPI Fraud Ring Detective: multi-hop, explainable, and it takes action." |

Optional extra shot: FalkorDB Browser → `MATCH p=(c:Case)-[]->() RETURN p` to show the cases inside the database.
