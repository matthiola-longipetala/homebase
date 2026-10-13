"""Stop ngrok for phonecall-mcp."""

import ctypes
import subprocess
import sys


def msgbox(title: str, message: str, style: int = 0x40):
    ctypes.windll.user32.MessageBoxW(0, message, title, style)


def is_ngrok_running() -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Get-Process ngrok -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "ngrok" in result.stdout
    except Exception:
        return False


if not is_ngrok_running():
    msgbox("phonecall-mcp ngrok", "ngrok is not running.")
    sys.exit(0)

subprocess.run(
    ["powershell", "-Command", "Stop-Process -Name ngrok -Force"],
    capture_output=True, timeout=5,
    creationflags=subprocess.CREATE_NO_WINDOW,
)

msgbox("phonecall-mcp ngrok", "ngrok tunnel stopped.")
