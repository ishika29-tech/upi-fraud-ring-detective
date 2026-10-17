"""
mcp_server.py - the SAME graph tools, exposed through MCP (Model Context Protocol).

MCP is a standard way for AI apps (Claude Desktop, Cursor, VS Code, ...) to discover
and call tools. Run this server and any MCP client can see our FalkorDB tools,
read their descriptions, and CHOOSE which one to call - no code from our agent needed.

Run (stdio, for Claude Desktop / Cursor):   python mcp_server.py
Try it in the browser inspector:            mcp dev mcp_server.py

This file is optional: the Streamlit app works without it.
"""
from mcp.server.fastmcp import FastMCP

import tools

mcp = FastMCP("upi-fraud-ring-detective")


# Each function below just calls the matching function in tools.py and returns the
# summary + data + the exact Cypher (so the MCP client can show its reasoning too).
def _out(res):
    return {"summary": res["summary"], "data": res["data"], "cypher": res["cypher"]}


@mcp.tool()
def list_flagged_transactions(limit: int = 20) -> dict:
    """List recent flagged UPI transactions (victim complaints / bank rules). Good place to start."""
    return _out(tools.list_flagged_transactions(limit))


@mcp.tool()
def get_transaction(txn_id: str) -> dict:
    """Look up one transaction: payer, payee, amount, time and flag reason."""
    return _out(tools.get_transaction(txn_id))


@mcp.tool()
def get_account_profile(account_id: str) -> dict:
    """Account age, KYC, UPI IDs, phones, devices, IPs and money in/out."""
    return _out(tools.get_account_profile(account_id))


@mcp.tool()
def find_shared_identifiers(account_id: str) -> dict:
    """Other accounts sharing a phone number, device or IP with this account (multi-hop)."""
    return _out(tools.find_shared_identifiers(account_id))


@mcp.tool()
def trace_money_flow(account_id: str, direction: str = "out", max_hops: int = 4, max_gap_minutes: int = 120) -> dict:
    """Follow the money up to max_hops hops in ('in') or out ('out') of an account, keeping rapid chains only."""
    return _out(tools.trace_money_flow(account_id, direction, max_hops, max_gap_minutes))


@mcp.tool()
def find_fraud_ring(account_id: str) -> dict:
    """FalkorDB algo.WCC (connected components) over shared phones/devices/IPs: the whole ring around an account."""
    return _out(tools.find_fraud_ring(account_id))


@mcp.tool()
def rank_ring_members(account_ids: list[str]) -> dict:
    """FalkorDB algo.pageRank over payments: find the collector/kingpin and pass-through mules."""
    return _out(tools.rank_ring_members(account_ids))


@mcp.tool()
def shortest_path_between(from_account: str, to_account: str) -> dict:
    """Shortest connection between two accounts through any link."""
    return _out(tools.shortest_path_between(from_account, to_account))


@mcp.tool()
def check_past_cases(account_id: str) -> dict:
    """Earlier investigation cases linked to this account or its phone/device/IP (agent memory)."""
    return _out(tools.check_past_cases(account_id))


@mcp.tool()
def compute_risk_score(account_ids: list[str]) -> dict:
    """Transparent 0-100 risk score per account with written reasons and a suggested action."""
    return _out(tools.compute_risk_score(account_ids))


@mcp.tool()
def take_action(account_ids: list[str], action: str, reason: str, risk_score: int = 0, txn_id: str = "") -> dict:
    """Write the decision into FalkorDB as a Case: FREEZE, ESCALATE, FLAG or CLEAR."""
    return _out(tools.take_action(account_ids, action, reason, risk_score, txn_id))


if __name__ == "__main__":
    mcp.run()  # stdio transport (what Claude Desktop and Cursor use)
