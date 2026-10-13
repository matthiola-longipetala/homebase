"""
picarx-mcp — robot control over the MCP protocol.

Runs on the Pi as an HTTP/SSE server reachable on the LAN. The operator is Claude Code:
it natively supports the remote SSE/HTTP MCP transport, and its Monitor tool provides the
out-of-band event activation that is key to physical presence.

Identity frame: the robot is the agent's tool for the physical space, not a separate
character. Tool names are function-level, docstrings are first-person ("what I see
through the camera").

Phase 1: only `status` + `move`. Camera/distance came in phase 2, the events queue in 3,
audio in 4, the reflex layer in 5.

The host/port go into the FastMCP CONSTRUCTOR (mcp 1.26.0), NOT into run().
"""
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# --- Config ---
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("ERROR: config.json missing! Copy config.example.json -> config.json.",
              file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


config = load_config()

# --- MCP server: host/port in the constructor (mcp 1.26.0) ---
mcp = FastMCP(
    name="picarx-mcp",
    # NO AUTHENTICATION: whoever reaches this port can use the camera, the wheels and the speaker.
    # Default = this Pi only. Set "host" to the Pi's LAN IP so the laptop can connect, and limit the port
    # to the laptop with a firewall rule (e.g. ufw allow from <laptop-ip> to any port 8765).
    host=config.get("host", "127.0.0.1"),
    port=config.get("port", 8765),
)

# --- active/passive mode timeout from the config ---
from core import robot_state                     # noqa: E402
robot_state.configure(config.get("active_timeout_s", 60))

# --- tool registration ---
from tools.status import register_status_tool   # noqa: E402
from tools.move import register_move_tool       # noqa: E402
from tools.look import register_look_tool       # noqa: E402
from tools.scan import register_scan_tool       # noqa: E402

register_status_tool(mcp, config)
register_move_tool(mcp, config)
register_look_tool(mcp, config)
register_scan_tool(mcp, config)

# --- live video stream supervisor (tied to active mode) ---
def _start_stream_supervisor() -> None:
    """Starts the stream_supervisor daemon in the background (guarded).

    The stream is tied to ACTIVE mode: the supervisor watches the /tmp/robot_active_at
    timestamp (written by mark_chat_active), keeps the stream alive while active and
    releases the camera when passive. In the idle state (nobody working) the camera
    pipeline does NOT run, only the lightweight supervisor loop. No error here may
    break the MCP server — the stream is only an add-on."""
    import subprocess
    from pathlib import Path
    sup = Path(__file__).parent / "stream" / "stream_supervisor.py"
    pidfile = "/tmp/stream_supervisor.pid"
    try:
        # do not start twice
        if Path(pidfile).exists():
            pid = int(Path(pidfile).read_text().strip())
            try:
                os.kill(pid, 0)
                return   # already running
            except OSError:
                pass
        if sup.exists():
            log = open("/tmp/stream_supervisor.log", "ab", buffering=0)
            subprocess.Popen(
                [sys.executable, str(sup)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
            print("stream_supervisor started (tied to active mode)", file=sys.stderr)
    except Exception as e:
        print(f"stream_supervisor start skipped: {e}", file=sys.stderr)


import os  # noqa: E402

# --- start ---
if __name__ == "__main__":
    print(f"picarx-mcp on {mcp.settings.host}:{mcp.settings.port}/sse", file=sys.stderr)
    _start_stream_supervisor()
    mcp.run(transport="sse")
