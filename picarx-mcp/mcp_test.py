"""Quick MCP client self-test: connects to the SSE server, lists the tools and calls
status (which lazily initialises Picarx() and reads from the hardware). For
verification only, not part of the running server."""
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client


def _server_url():
    """The SSE address from config.json next to this file (the server may listen on the LAN IP only)."""
    import json, os
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    host = c.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{c.get('port', 8765)}/sse"


async def main():
    async with sse_client(_server_url()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            res = await session.call_tool("status", {})
            print("STATUS_RESULT:")
            for c in res.content:
                if getattr(c, "type", None) == "text":
                    print(c.text)


asyncio.run(main())
