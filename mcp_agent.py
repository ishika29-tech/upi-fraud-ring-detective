"""
mcp_agent.py - the agent, but it discovers and calls its tools THROUGH MCP.

agent.py imports the tools directly (simple, fast - used by the UI).
This file shows the "proper" MCP way: it starts mcp_server.py, ASKS it which tools
exist (list_tools), gives that list to the LLM, and every tool call the LLM chooses
goes back over MCP (call_tool). Swap in any other MCP server and the agent can use it.

Run:  python mcp_agent.py TXN502640          (uses LLM_PROVIDER from .env: gemini or openai)
"""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent import MAX_STEPS, SYSTEM_PROMPT, make_gemini_client

load_dotenv()


async def call_mcp_tool(session, name, args):
    result = await session.call_tool(name, args)
    return result.content[0].text if result.content else "{}"


async def run_with_openai(session, mcp_tools, task):
    from openai import OpenAI
    client = OpenAI()
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.inputSchema}} for t in mcp_tools]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task}]
    for _ in range(MAX_STEPS):
        msg = client.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                                             messages=messages, tools=tools).choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            return msg.content
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            print(f"-> [MCP] {call.function.name}({args})")
            text = await call_mcp_tool(session, call.function.name, args)
            print("   ", json.loads(text).get("summary", "")[:160])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text[:6000]})
    return "Stopped: step limit."


async def run_with_gemini(session, mcp_tools, task):
    from google.genai import types
    client = make_gemini_client()
    decls = [types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.inputSchema)
             for t in mcp_tools]
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT,
                                         tools=[types.Tool(function_declarations=decls)])
    contents = [types.Content(role="user", parts=[types.Part(text=task)])]
    for _ in range(MAX_STEPS):
        response = client.models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                                                  contents=contents, config=config)
        contents.append(response.candidates[0].content)
        if not response.function_calls:
            return response.text
        parts = []
        for call in response.function_calls:
            args = dict(call.args or {})
            print(f"-> [MCP] {call.name}({args})")
            text = await call_mcp_tool(session, call.name, args)
            print("   ", json.loads(text).get("summary", "")[:160])
            parts.append(types.Part.from_function_response(name=call.name, response={"result": text[:6000]}))
        contents.append(types.Content(role="user", parts=parts))
    return "Stopped: step limit."


async def main(txn_id):
    server = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            print(f"MCP server offers {len(mcp_tools)} tools: {[t.name for t in mcp_tools]}\n")
            task = f"Investigate flagged UPI transaction {txn_id}. Find the full fraud ring (if any), act, and explain."
            if os.getenv("LLM_PROVIDER", "gemini") == "openai":
                answer = await run_with_openai(session, mcp_tools, task)
            else:
                answer = await run_with_gemini(session, mcp_tools, task)
            print("\n" + (answer or ""))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "TXN502640"))
