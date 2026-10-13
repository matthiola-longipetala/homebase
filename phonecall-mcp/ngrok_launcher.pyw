"""ngrok launcher for phonecall-mcp with Windows notifications."""

import ctypes
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
import os

SCRIPT_DIR = Path(__file__).parent
NGROK_EXE = SCRIPT_DIR / "ngrok.exe"

load_dotenv(SCRIPT_DIR / ".env")

_ngrok_url = os.getenv("NGROK_URL", "")
DOMAIN = urlparse(_ngrok_url).hostname if _ngrok_url else None
PORT = 8765

# MessageBox styles
MB_ABORTRETRYIGNORE = 0x02
MB_ICONQUESTION = 0x20
MB_ICONERROR = 0x10
MB_ICONINFO = 0x40
IDABORT = 3
IDRETRY = 4
IDIGNORE = 5


def notify(title: str, message: str):
    """Show a Windows balloon notification via PowerShell."""
    # Escape quotes for PowerShell
    title_safe = title.replace('"', '`"')
    message_safe = message.replace('"', '`"')
    ps_cmd = f'''
    Add-Type -AssemblyName System.Windows.Forms
    $n = New-Object System.Windows.Forms.NotifyIcon
    $n.Icon = [System.Drawing.SystemIcons]::Information
    $n.BalloonTipTitle = "{title_safe}"
    $n.BalloonTipText = "{message_safe}"
    $n.Visible = $true
    $n.ShowBalloonTip(5000)
    Start-Sleep -Seconds 3
    $n.Dispose()
    '''
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def msgbox(title: str, message: str, style: int = MB_ICONINFO):
    """Show a Windows message box. Returns button ID."""
    return ctypes.windll.user32.MessageBoxW(0, message, title, style)


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


def stop_ngrok():
    """Kill all ngrok processes."""
    subprocess.run(
        ["powershell", "-Command", "Stop-Process -Name ngrok -Force -ErrorAction SilentlyContinue"],
        capture_output=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(1)


def start_ngrok():
    """Start ngrok and wait for it to run."""
    if not NGROK_EXE.exists():
        msgbox("phonecall-mcp ngrok", f"Error: ngrok.exe not found!\n{NGROK_EXE}", MB_ICONERROR)
        sys.exit(1)

    process = subprocess.Popen(
        [str(NGROK_EXE), "http", str(PORT), f"--domain={DOMAIN}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    time.sleep(3)

    if process.poll() is None:
        notify("phonecall-mcp ngrok", f"Tunnel active!\n{DOMAIN}")
        # Wait for ngrok to stop
        process.wait()
        notify("phonecall-mcp ngrok", "ngrok tunnel stopped!")
    else:
        msgbox("phonecall-mcp ngrok", "Error: ngrok failed to start!", MB_ICONERROR)


# --- Main ---

if not DOMAIN:
    msgbox("phonecall-mcp ngrok", "Error: NGROK_URL not set in .env file!", MB_ICONERROR)
    sys.exit(1)

if is_ngrok_running():
    # Already running -> show 3-button dialog
    choice = msgbox(
        "phonecall-mcp ngrok",
        "ngrok tunnel is already running!\n\n"
        "Abort = Stop\n"
        "Retry = Restart\n"
        "Ignore = Keep running",
        MB_ABORTRETRYIGNORE | MB_ICONQUESTION,
    )

    if choice == IDABORT:
        # Stop
        stop_ngrok()
        notify("phonecall-mcp ngrok", "ngrok tunnel stopped.")
        sys.exit(0)

    elif choice == IDRETRY:
        # Restart
        stop_ngrok()
        start_ngrok()

    elif choice == IDIGNORE:
        # Do nothing
        sys.exit(0)

else:
    # Not running -> start it
    start_ngrok()
