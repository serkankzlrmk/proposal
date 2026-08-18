#!/usr/bin/env python3
"""Full NotebookLM flow: health -> auth check -> list notebooks -> ask question."""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def call(session, tool, args=None):
    result = await session.call_tool(tool, args or {})
    texts = []
    for block in result.content:
        if hasattr(block, "text"):
            texts.append(block.text)
        else:
            texts.append(str(block))
    return "\n".join(texts)

async def main():
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "notebooklm-mcp@latest"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            health_raw = await call(session, "get_health")
            print("=== HEALTH ===")
            print(health_raw)
            health = json.loads(health_raw.split("\n")[-1] if "{" in health_raw else health_raw)
            if isinstance(health, str):
                health = json.loads(health)
            data = health.get("data", health)
            if data.get("authenticated"):
                print("\n=== AUTH OK, listing notebooks ===")
                print(await call(session, "list_notebooks"))
            else:
                print("\n=== NOT AUTHENTICATED ===")

asyncio.run(main())
