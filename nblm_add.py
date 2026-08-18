#!/usr/bin/env python3
"""Add a notebook to the library, verify, then query it."""
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

            print("=== ADD NOTEBOOK ===")
            add_result = await call(session, "add_notebook", {
                "url": "https://notebook.google.com/notebook/6c22031a-b83e-4768-9a0b-d22afe5205b8",
                "name": "GMS Proposals and PDF Generation Tool Comparisons",
                "description": "GMS proposals work and PDF generation tool comparisons",
                "topics": ["GMS proposals", "PDF generation tools"],
                "content_types": ["proposals", "tool comparisons"],
            })
            print(add_result[:2000])

            print("\n=== LIST NOTEBOOKS ===")
            print(await call(session, "list_notebooks"))

            print("\n=== SELECT NOTEBOOK (make active) ===")
            print(await call(session, "select_notebook", {
                "id": "6c22031a-b83e-4768-9a0b-d22afe5205b8"
            }))

asyncio.run(main())
