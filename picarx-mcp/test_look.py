"""Verification: lists the tools and calls look, checks whether an image came back
(image content, not empty)."""
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
            res = await session.call_tool("look", {"pan": 0, "tilt": 0})
            for c in res.content:
                t = getattr(c, "type", None)
                if t == "image":
                    data = getattr(c, "data", "")
                    print(f"IMAGE OK: image/{getattr(c,'mimeType','?')}, base64 length={len(data)}")
                elif t == "text":
                    print("TEXT:", c.text[:200])


asyncio.run(main())
