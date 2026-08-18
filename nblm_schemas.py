#!/usr/bin/env python3
"""Show add_notebook / list_notebooks tool schemas."""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "notebooklm-mcp@latest"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                if t.name in ("add_notebook", "select_notebook", "list_notebooks", "ask_question"):
                    print(f"=== {t.name} ===")
                    print(json.dumps(t.inputSchema, indent=2)[:1500])
                    print()

asyncio.run(main())
