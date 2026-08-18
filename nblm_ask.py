#!/usr/bin/env python3
"""Query the GMS Proposals notebook."""
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
            question = sys.argv[1] if len(sys.argv) > 1 else "What is this notebook about? Summarize the main topics and contents."
            fmt = sys.argv[2] if len(sys.argv) > 2 else "footnotes"
            print("=== QUESTION ===")
            print(question)
            print("\n=== ANSWER ===")
            result = await call(session, "ask_question", {
                "question": question,
                "notebook_id": "gms-proposals-and-pdf-generati",
                "source_format": fmt,
            })
            print(result[:6000])

asyncio.run(main())
