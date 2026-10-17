"""
agent.py - the AI agent.

An "agent" here is just a loop:

    1. Send the task + the list of tools to the LLM.
    2. The LLM answers with "please call tool X with arguments Y" (a function call).
    3. We run that tool (a Cypher query on FalkorDB) and send the result back.
    4. Repeat until the LLM stops asking for tools and writes its final answer.

`investigate()` is a Python *generator*: it `yield`s one event per step, so the
Streamlit UI can show each step live while the agent is still working.

Providers (set LLM_PROVIDER in .env):
    gemini   - Google Gemini (default)
    openai   - OpenAI
    offline  - no LLM at all: runs the same tools in a fixed order (good for
               learning, and a safe backup if your free API quota runs out)
"""
import json
import os
import time

from dotenv import load_dotenv

from tools import TOOLS, for_llm, run_tool

load_dotenv()

MAX_STEPS = 12  # safety limit so the loop can never run forever

SYSTEM_PROMPT = """You are a UPI fraud investigator for an Indian bank. You investigate by calling tools that
query a FalkorDB graph of accounts, UPI IDs, phones, devices, IPs and payments (PAID edges).

How to investigate a flagged transaction:
1. get_transaction, then look at the RECEIVING account (and the payer if it looks odd).
2. Expand multi-hop: find_shared_identifiers, find_fraud_ring (connected components), and
   trace_money_flow in BOTH directions (in = where money came from, out = where it went).
3. check_past_cases for memory of earlier investigations.
4. compute_risk_score on every suspicious account you found (include accounts from the ring AND the money trail,
   but NOT obvious victims who only sent money once). Use rank_ring_members to find the collector.
5. Decide and act with take_action (you may call it more than once, e.g. FREEZE high-risk accounts and
   ESCALATE borderline ones). Every suspicious ring member should get an action: FREEZE (score >= 60),
   ESCALATE (40-59) or FLAG (25-39) - do not leave mules untouched. If the evidence shows no fraud (e.g. an old family sharing a device), use CLEAR on the accounts
   that were under suspicion (not on every shop or person the money touched).
   Never freeze an account on a single weak signal.

Be efficient: when several tool calls do not depend on each other, request them together in one turn.
Your final answer (after take_action) must be short and use exactly these headings:
VERDICT: <FREEZE / ESCALATE / FLAG / CLEAR> - one line
RING: account ids and their role
PATH: the key graph path(s), e.g. ACC1 -[PAID ₹50,000]-> ACC2 -[USES_DEVICE]-> DEV-1 <-[USES_DEVICE]- ACC3
WHY: 3-5 bullet points citing graph evidence
"""


# ----------------------------------------------------------------------------
# Gemini
# ----------------------------------------------------------------------------
def make_gemini_client():
    from google import genai
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def run_gemini(task):
    from google.genai import errors, types

    client = make_gemini_client()
    # Free Gemini keys have small DAILY limits per model, so we keep a list of models to fall back to.
    models = [os.getenv("GEMINI_MODEL", "gemini-flash-latest")] + [
        m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-3.1-flash-lite").split(",")
        if m.strip()]
    models = list(dict.fromkeys(models))  # remove duplicates, keep order
    declarations = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                              parameters_json_schema=t["parameters"]) for t in TOOLS]
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT,
                                         tools=[types.Tool(function_declarations=declarations)],
                                         temperature=0.2)
    contents = [types.Content(role="user", parts=[types.Part(text=task)])]

    for step in range(MAX_STEPS):
        retries = 0
        while True:
            try:
                response = client.models.generate_content(model=models[0], contents=contents, config=config)
                break
            except (errors.ClientError, errors.ServerError) as e:
                if e.code == 503 and retries < 3:  # model busy for a moment -> wait and retry
                    retries += 1
                    time.sleep(3 * retries)
                elif e.code in (429, 503) and len(models) > 1:  # quota used up -> try the next model
                    yield {"type": "info", "text": f"{models[0]} unavailable ({e.code}), switching to {models[1]}."}
                    models.pop(0)
                    retries = 0
                else:
                    raise
        contents.append(response.candidates[0].content)  # keep the model's turn in the history
        calls = response.function_calls or []
        thought = "".join(p.text for p in response.candidates[0].content.parts if p.text) if not calls else ""
        if not calls:  # no more tools wanted -> final answer
            yield {"type": "final", "text": thought or response.text or ""}
            return
        reply_parts = []
        for call in calls:
            args = dict(call.args or {})
            yield {"type": "tool_call", "tool": call.name, "args": args}
            tool_result = run_tool(call.name, args)
            yield {"type": "tool_result", "tool": call.name, "args": args, "result": tool_result}
            reply_parts.append(types.Part.from_function_response(name=call.name,
                                                                 response={"result": for_llm(tool_result)}))
        contents.append(types.Content(role="user", parts=reply_parts))
    yield {"type": "final", "text": "Stopped: reached the step limit."}


# ----------------------------------------------------------------------------
# OpenAI
# ----------------------------------------------------------------------------
def run_openai(task):
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY from the environment
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["parameters"]}} for t in TOOLS]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task}]

    for step in range(MAX_STEPS):
        response = client.chat.completions.create(model=model, messages=messages, tools=tools, temperature=0.2)
        msg = response.choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            yield {"type": "final", "text": msg.content or ""}
            return
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            yield {"type": "tool_call", "tool": call.function.name, "args": args}
            tool_result = run_tool(call.function.name, args)
            yield {"type": "tool_result", "tool": call.function.name, "args": args, "result": tool_result}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": for_llm(tool_result)})
    yield {"type": "final", "text": "Stopped: reached the step limit."}


# ----------------------------------------------------------------------------
# Offline mode: the same investigation, but the "decisions" are simple if/else rules.
# Compare this with the LLM version to understand what the LLM is doing for you!
# ----------------------------------------------------------------------------
def run_offline(task, txn_id):
    def step(tool, **args):
        events = [{"type": "tool_call", "tool": tool, "args": args}]
        res = run_tool(tool, args)
        events.append({"type": "tool_result", "tool": tool, "args": args, "result": res})
        return res, events

    res, ev = step("get_transaction", txn_id=txn_id)
    yield from ev
    if not res["data"]:
        yield {"type": "final", "text": res["summary"]}
        return
    payer, payee = res["data"]["from_id"], res["data"]["to_id"]
    suspects = {payee}
    for tool, args in [("get_account_profile", {"account_id": payee}),
                       ("find_shared_identifiers", {"account_id": payee}),
                       ("find_fraud_ring", {"account_id": payee}),
                       ("trace_money_flow", {"account_id": payee, "direction": "out"}),
                       ("trace_money_flow", {"account_id": payee, "direction": "in", "max_hops": 2}),
                       ("check_past_cases", {"account_id": payee})]:
        res, ev = step(tool, **args)
        yield from ev
        if tool == "find_fraud_ring":
            suspects.update(a["id"] for a in res["data"]["ring_accounts"])
        if tool == "trace_money_flow" and args["direction"] == "out":
            suspects.update(res["data"]["accounts_involved"])
    res, ev = step("compute_risk_score", account_ids=sorted(suspects))
    yield from ev
    scores = res["data"]["scores"]
    freeze = [s["id"] for s in scores if s["risk_score"] >= 60]
    review = [s["id"] for s in scores if 40 <= s["risk_score"] < 60]
    if freeze or review:
        res, ev = step("rank_ring_members", account_ids=freeze + review)
        yield from ev
    if freeze:
        res, ev = step("take_action", account_ids=freeze, action="FREEZE", txn_id=txn_id,
                       risk_score=max(s["risk_score"] for s in scores),
                       reason="Rule-based: risk score >= 60 (shared identifiers / rapid forwarding / new accounts)")
        yield from ev
    if review:
        res, ev = step("take_action", account_ids=review, action="ESCALATE", txn_id=txn_id,
                       risk_score=max(s["risk_score"] for s in scores if s["id"] in review),
                       reason="Rule-based: borderline risk 40-59, needs a human analyst")
        yield from ev
    if not freeze and not review:
        res, ev = step("take_action", account_ids=[payee], action="CLEAR", txn_id=txn_id,
                       risk_score=scores[0]["risk_score"] if scores else 0,
                       reason="Rule-based: no strong fraud signals in the graph")
        yield from ev
    verdict = "FREEZE" if freeze else ("ESCALATE" if review else "CLEAR")
    lines = [f"VERDICT: {verdict} (offline rule-based mode)",
             f"RING: freeze {', '.join(freeze) or '-'}; review {', '.join(review) or '-'}",
             "WHY:"] + [f"- {s['id']} ({s['risk_score']}): {'; '.join(s['reasons'][:3])}" for s in scores[:5]]
    yield {"type": "final", "text": "\n".join(lines)}


# ----------------------------------------------------------------------------
# The one function the UI / command line calls
# ----------------------------------------------------------------------------
def investigate(txn_id, provider=None):
    provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower()
    task = f"Investigate flagged UPI transaction {txn_id}. Find the full fraud ring (if any), act, and explain."
    yield {"type": "info", "text": f"Starting investigation of {txn_id} with provider '{provider}'."}
    try:
        if provider == "openai":
            yield from run_openai(task)
        elif provider == "offline":
            yield from run_offline(task, txn_id)
        else:
            yield from run_gemini(task)
    except Exception as e:
        yield {"type": "error", "text": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # Command line:  python agent.py TXN502640  [gemini|openai|offline]
    import sys

    txn = sys.argv[1] if len(sys.argv) > 1 else "TXN502640"
    for event in investigate(txn, sys.argv[2] if len(sys.argv) > 2 else None):
        if event["type"] == "tool_call":
            print(f"\n-> {event['tool']}({event['args']})")
        elif event["type"] == "tool_result":
            print(f"   {event['result']['summary']}")
        else:
            print(f"\n[{event['type']}] {event['text']}")
