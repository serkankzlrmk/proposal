#!/usr/bin/env python3
"""Minimal NotebookLM MCP client - lists notebooks and answers questions."""
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
            tool = sys.argv[1] if len(sys.argv) > 1 else "list_notebooks"
            args_json = sys.argv[2] if len(sys.argv) > 2 else "{}"
            args = json.loads(args_json)
            result = await session.call_tool(tool, args)
            # result.content is a list of TextContent/ImageContent blocks
            for block in result.content:
                if hasattr(block, "text"):
                    print(block.text)
                else:
                    print(str(block))
            if result.isError:
                print("ERROR", result.isError, file=sys.stderr)

asyncio.run(main())
