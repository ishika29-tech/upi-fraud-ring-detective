"""
db.py - the ONE place that knows how to talk to FalkorDB.

Every other file calls `run_query(...)` from here, so if you move from
local Docker to FalkorDB Cloud you only change the .env file, never the code.
"""
import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()  # reads the .env file in this folder (if there is one)

GRAPH_NAME = os.getenv("GRAPH_NAME", "upi_fraud")

_graph = None  # we connect once and reuse the connection


def get_graph():
    """Connect to FalkorDB (only the first time) and return our graph."""
    global _graph
    if _graph is None:
        db = FalkorDB(
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", "6379")),
            username=os.getenv("FALKORDB_USERNAME") or None,  # only needed for FalkorDB Cloud
            password=os.getenv("FALKORDB_PASSWORD") or None,
        )
        _graph = db.select_graph(GRAPH_NAME)
    return _graph


def run_query(cypher, params=None):
    """
    Run a Cypher query and return the rows as a list of dicts.

    Example:
        run_query("MATCH (a:Account {id: $id}) RETURN a.holder AS name", {"id": "ACC1001"})
        -> [{"name": "Rohan Sharma"}]
    """
    result = get_graph().query(cypher, params or {})
    columns = [col[1] for col in result.header]  # header = [[type, name], ...]
    return [dict(zip(columns, row)) for row in result.result_set]
