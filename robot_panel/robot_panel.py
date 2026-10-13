#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""robot_panel.py — DESKTOP ROBOT CONTROL PANEL.

One file, two faces:
  • GUI (for the owner):  pythonw robot_panel.py          → big buttons, starts from a desktop icon
  • CLI (for the agent):  python robot_panel.py allapot
                          python robot_panel.py leallit --igen

WHY THIS SHAPE (owner's request): the owner wanted to be able to handle the robot herself when the
agent is unavailable, with the same tool the agent uses. The gap it fills: a trivial operation
(put the robot to sleep) used to depend on the bridge and the MCP server running, and ssh.exe dies
silently when launched from the Windows-MCP PowerShell channel. So the same file now serves both.

★ HENCE PARAMIKO, NOT ssh.exe: a pure-Python SSH client sidesteps that failure.

FUNCTIONS — ONLY THESE TWO (by design, keep it small):
  1. Status + battery
  2. Graceful shutdown (with confirmation, `leallit.py` on the robot)

Prerequisite: pip install paramiko
Key: PI_KEY env var or {HOME}/.ssh/<key>  — the code never contains a password or key.
Host discovery: PI_HOST env var, then the saved last IP, then PI_MDNS_HOST, then — ONLY if PI_SCAN_SUBNET=1 — a /24
SSH-port scan (a port scan of the local network: fine at home, not on a work or hotel network, so it is opt-in).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CACHE = APP_DIR / "robot_panel_cache.json"

# The Windows console defaults to cp852/cp1250 — accented characters would be garbled and
# emoji would raise UnicodeEncodeError. (This is not cosmetic: Claude reads the CLI over a
# machine channel.)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROBE_HOST = os.environ.get("ROBOT_PANEL_PROBE_HOST", "8.8.8.8")  # public resolver, only used to pick the local interface
SSH_USER = os.environ.get("PI_USER", "pi")
SSH_PORT = 22
KEY_PATH = Path(os.environ.get("PI_KEY") or (Path.home() / ".ssh" / "id_ed25519"))
MDNS_HOST = os.environ.get("PI_MDNS_HOST", "")  # e.g. "<robot>.local"; empty = skip mDNS
REMOTE_DIR = "{PI_HOME}/picarx-mcp"

# From battery_guard.py (2-cell Li): full ~8.4 V, "empty"/near-brownout ~6.0 V
FULL_V, EMPTY_V = 8.4, 6.0
WARN_V, CRIT_V = 6.9, 6.6
# a /tmp/battery_status older than this is no longer fresh (battery_guard measures every 20 s)
BAT_STALE_S = 120

CONNECT_TIMEOUT = 6
CMD_TIMEOUT = 25
SHUTDOWN_TIMEOUT = 90


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def vpct(v: float) -> int:
    """Voltage → percent, with the same formula as battery_guard.py."""
    return max(0, min(100, round((v - EMPTY_V) / (FULL_V - EMPTY_V) * 100)))


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(d: dict) -> None:
    try:
        CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # the cache is a convenience; if it fails, everything still works


def port_open(ip: str, port: int = SSH_PORT, timeout: float = 0.4) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    finally:
        s.close()


def local_ipv4() -> str | None:
    """The machine's primary IPv4 (UDP trick: sends no packet, only picks a route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((PROBE_HOST, 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def scan_subnet(base_ip: str, log=print) -> list[str]:
    """SSH-port search on the /24. Lesson from the travel-hotspot setup:
    apart from the robot, rarely anything listens on port 22, and Defender stops a ping-based sweep.

    ⚠️ Returns EVERY hit, not just the first: otherwise a router/NAS with port 22 open
    would be a false positive, and we would wrongly report the robot as "unreachable". The
    decision is made by the KEY-BASED LOGIN (see Robot.connect), not by the open port."""
    prefix = base_ip.rsplit(".", 1)[0]
    log(f"   /24 scan: {prefix}.2-254, SSH port …")
    candidates = [f"{prefix}.{i}" for i in range(2, 255) if f"{prefix}.{i}" != base_ip]
    with ThreadPoolExecutor(max_workers=64) as ex:
        found = [ip for ip, alive in zip(candidates, ex.map(port_open, candidates)) if alive]
    log(f"   … {len(found)} machine(s) listening on port 22: {', '.join(found) or '(none)'}")
    return found


def candidate_hosts(log=print) -> list[str]:
    """Order of attempts: PI_HOST env → saved last IP → PI_MDNS_HOST → /24 SSH-port scan.
    ⚠️ If none of them works, the caller SAYS SO (silent-ceiling guard) — it does not spin quietly."""
    out: list[str] = []

    env_host = os.environ.get("PI_HOST")
    if env_host:
        log(f"   trying: PI_HOST ({env_host}) …")
        if port_open(env_host):
            log("   … answers.")
            out.append(env_host)
        else:
            log("   … no answer.")

    cache = load_cache()
    last = cache.get("last_ip")
    if last:
        log(f"   trying: saved IP ({last}) …")
        if port_open(last):
            log("   … answers.")
            out.append(last)
        else:
            log("   … no answer.")

    log(f"   trying: {MDNS_HOST or '(no mDNS host set)'} …")
    try:
        if not MDNS_HOST:
            raise OSError("PI_MDNS_HOST not set")
        ip = socket.getaddrinfo(MDNS_HOST, SSH_PORT, socket.AF_INET)[0][4][0]
        if port_open(ip) and ip not in out:
            log(f"   … resolved: {ip}")
            out.append(ip)
        elif ip in out:
            log("   … same as the saved IP.")
        else:
            log("   … resolved, but port 22 is closed.")
    except Exception:
        log("   … mDNS did not resolve it (this is normal on new networks).")

    if not out and os.environ.get("PI_SCAN_SUBNET") != "1":
        log("   … not found. Set PI_HOST to the robot's IP, or PI_SCAN_SUBNET=1 to scan this /24 for an SSH port.")
    elif not out:
        mine = local_ipv4()
        if mine:
            out.extend(ip for ip in scan_subnet(mine, log) if ip not in out)
        else:
            log("   ⚠️ Cannot even determine this machine's own IPv4 — is there a network?")
    return out


# --------------------------------------------------------------------------------------
# SSH
# --------------------------------------------------------------------------------------

class Robot:
    def __init__(self, log=print):
        self.log = log
        self.ip: str | None = None
        self.cli = None

    def connect(self) -> bool:
        try:
            import paramiko
        except ImportError:
            self.log("ERROR: paramiko is not installed.  Fix:  pip install paramiko")
            return False

        if not KEY_PATH.exists():
            self.log(f"ERROR: SSH key not found: {KEY_PATH}")
            return False

        self.log("Looking for the robot …")
        hosts = candidate_hosts(self.log)
        if not hosts:
            self._nem_elerheto("no machine is listening on port 22")
            return False

        last_err = None
        for ip in hosts:
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                cli.connect(
                    ip, port=SSH_PORT, username=SSH_USER,
                    key_filename=str(KEY_PATH),
                    timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT,
                    auth_timeout=CONNECT_TIMEOUT, look_for_keys=False, allow_agent=False,
                )
            except Exception as e:
                last_err = e
                if len(hosts) > 1:
                    self.log(f"   {ip}: not the robot ({type(e).__name__})")
                cli.close()
                continue

            self.log(f"   ✅ logged in: {ip}")
            self.cli = cli
            self.ip = ip
            cache = load_cache()
            cache["last_ip"] = ip
            cache["last_ok"] = datetime.now().isoformat(timespec="seconds")
            save_cache(cache)
            return True

        self._nem_elerheto(f"key-based login failed everywhere — last error: {last_err}")
        return False

    def _nem_elerheto(self, reszlet: str) -> None:
        self.log("")
        self.log("❌ THE ROBOT IS NOT REACHABLE.")
        self.log(f"   ({reszlet})")
        self.log("   Check: is it switched on · is the green ACT LED lit ·")
        self.log("   are the computer and the robot on the same wifi.")

    def run(self, cmd: str, timeout: int = CMD_TIMEOUT) -> tuple[int, str, str]:
        assert self.cli is not None
        _in, out, err = self.cli.exec_command(cmd, timeout=timeout)
        so = out.read().decode("utf-8", "replace")
        se = err.read().decode("utf-8", "replace")
        return out.channel.recv_exit_status(), so, se

    def close(self) -> None:
        if self.cli:
            try:
                self.cli.close()
            except Exception:
                pass
            self.cli = None


# --------------------------------------------------------------------------------------
# 1) STATUS + BATTERY
# --------------------------------------------------------------------------------------

# Everything in a single SSH session — many small exec_command calls are slow and fragile.
STATUS_CMD = r"""
echo "@@BAT"; cat /tmp/battery_status 2>/dev/null
echo "@@BATAGE"; expr $(date +%s) - $(stat -c %Y /tmp/battery_status 2>/dev/null || echo 0) 2>/dev/null
echo "@@GUARD"; systemctl is-active battery_guard 2>/dev/null
echo "@@WIFI"; iwgetid -r 2>/dev/null || nmcli -t -f active,ssid dev wifi 2>/dev/null | sed -n 's/^yes://p' | head -1
echo "@@IP"; hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -2 | tr '\n' ' '; echo
echo "@@UP"; uptime -p 2>/dev/null
echo "@@TEMP"; cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null
echo "@@DISK"; df -h / 2>/dev/null | tail -1 | awk '{print $4" free ("$5" full)"}'
echo "@@PROC"; pgrep -af 'server\.py|orszem\.py|robot_listen_loop|bridge\.py|go2rtc' 2>/dev/null
echo "@@END"
"""

# Only runs if the flag is missing or stale. ⚠️ This is I²C access — per lesson #34,
# simultaneous bus use can produce a corrupted reading, so it is NOT called routinely,
# only when battery_guard's flag is unusable.
LIVE_BAT_CMD = (
    "python3 -c \"from robot_hat import get_battery_voltage as g; print(round(float(g()),2))\" 2>/dev/null"
)


def proc_names(raw: str) -> list[str]:
    """The service NAME from `pgrep -af` lines. I did this with sed in the shell, and it left
    a quote in the output ("Running: orszem.py, " ") — text splitting is Python's job, not the shell's."""
    out: list[str] = []
    for line in raw.splitlines():
        name = None
        for tok in line.split():
            t = tok.strip("'\"").split("/")[-1]
            if t.endswith(".py") or t in ("go2rtc", "robot_listen_loop"):
                name = t
                break
        if name and name not in out:
            out.append(name)
    return out


def parse_status(raw: str) -> dict:
    blocks, key = {}, None
    for line in raw.splitlines():
        if line.startswith("@@"):
            key = line[2:].strip()
            blocks[key] = []
        elif key:
            blocks[key].append(line)
    return {k: "\n".join(v).strip() for k, v in blocks.items()}


def status_report(rb: Robot) -> str:
    rc, out, _err = rb.run(STATUS_CMD)
    b = parse_status(out)
    L: list[str] = []

    # ⚠️ NO EMOJI IN THIS BOX. The ScrolledText runs with Consolas, which has no emoji glyphs —
    # they showed up as empty squares. Keep emoji in the Segoe UI header, where they look nice.
    L.append(f"ALIVE        {rb.ip}")

    # --- battery ---
    v = pct = state = None
    age = None
    try:
        age = int(b.get("BATAGE", "").strip() or 0)
    except ValueError:
        age = None
    bat = b.get("BAT", "").strip()
    if bat:
        try:
            pv, pp, ps = bat.split()
            v, pct, state = float(pv), int(pp), ps
        except Exception:
            pass

    fresh = v is not None and age is not None and 0 <= age <= BAT_STALE_S
    if not fresh:
        # the flag is missing or stale → SAY SO, and measure live
        why = "no /tmp/battery_status" if not bat else f"the data is {age} s old (stale)"
        L.append(f"⚠️ The battery flag is unusable ({why}) → live measurement over I²C …")
        rc2, out2, _ = rb.run(LIVE_BAT_CMD, timeout=15)
        try:
            v = float(out2.strip().splitlines()[-1])
            pct = vpct(v)
            state = "CRIT" if v <= CRIT_V else ("WARN" if v <= WARN_V else "OK")
        except Exception:
            v = None

    if v is None:
        L.append("Battery      UNREADABLE (neither the flag nor the live measurement).")
    else:
        n = round(pct / 5)
        bar = "[" + "#" * n + "-" * (20 - n) + "]"
        friss = "fresh" if fresh else "live measurement"
        L.append(f"Battery      {pct:>3}%  {bar}  {v:.2f} V   [{state}, {friss}]")
        if state == "CRIT":
            L.append("             CRITICAL — battery_guard may shut it down on its own at any moment. CHARGE IT!")
        elif state == "WARN":
            L.append("             Low: the charger would be welcome, do not plan long drives.")

    guard = b.get("GUARD", "").strip()
    if guard != "active":
        L.append(f"battery_guard  {guard or 'cannot be queried'} — deep-discharge protection is NOT running!")

    # --- network / system ---
    wifi = b.get("WIFI", "").strip()
    ips = " ".join(b.get("IP", "").split())  # already filtered to IPv4 on the shell side
    L.append(f"Wifi         {wifi or '(unknown)'}")
    L.append(f"IP           {ips or rb.ip}")

    up = b.get("UP", "").strip()
    if up:
        L.append(f"Up           {up.replace('up ', '')}")

    try:
        t = int(b.get("TEMP", "0")) / 1000.0
        if t:
            L.append(f"CPU temp     {t:.1f} °C" + ("   ← HOT" if t >= 70 else ""))
    except Exception:
        pass

    disk = b.get("DISK", "").strip()
    if disk:
        L.append(f"SD card      {disk}")

    names = proc_names(b.get("PROC", ""))
    L.append("Running      " + (", ".join(names) if names else "(no known robot service)"))

    return "\n".join(L)


# --------------------------------------------------------------------------------------
# 2) GRACEFUL SHUTDOWN
# --------------------------------------------------------------------------------------

LED_FIGYELMEZTETES = (
    "!!  ONLY PRESS THE SWITCH ONCE THE GREEN ACT LED HAS GONE DARK FOR GOOD!\n"
    "   (At the end of shutdown it blinks ~10 times as a farewell — that is the sign: NOT YET.)\n"
    "   This is the lesson of the July 2026 SD-card death: cutting power during a write leaves damage."
)


def shutdown(rb: Robot, log=print) -> bool:
    log("Graceful shutdown starting …")
    rc, out, err = rb.run(
        f"cd {REMOTE_DIR} && PYTHONUTF8=1 python3 leallit.py", timeout=SHUTDOWN_TIMEOUT
    )
    for line in (out + err).splitlines():
        if line.strip():
            log("   " + line.rstrip())

    # The SSH connection may drop during shutdown — that is NOT an error but a sign of success.
    # NOTE: "sudo-engedély hiányzik" ("sudo permission missing") is matched against the output
    # of leallit.py on the robot; keep the two in sync.
    if rc != 0 and "sudo-engedély hiányzik" in (out + err):
        log("")
        log("❌ The shutdown did not complete: the password-less shutdown permission is missing.")
        log("   One-time setup in the robot's shell (needs sudo, the owner runs it):")
        log("     echo 'pi ALL=(ALL) NOPASSWD: /usr/sbin/shutdown' | sudo tee /etc/sudoers.d/010_pi-shutdown")
        log("     sudo chmod 440 /etc/sudoers.d/010_pi-shutdown")
        return False

    log("")
    log("✅ The shutdown command was sent.")
    log(LED_FIGYELMEZTETES)
    return True


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def cli_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="robot_panel.py",
        description="Robot panel — status and graceful shutdown (GUI: no arguments).",
    )
    ap.add_argument("parancs", choices=["allapot", "leallit", "gui"],
                    help="allapot = status+battery · leallit = graceful shutdown · gui = window")
    ap.add_argument("--igen", action="store_true",
                    help="for leallit: confirm without asking (for non-interactive calls)")
    a = ap.parse_args(argv)

    if a.parancs == "gui":
        return gui_main()

    rb = Robot()
    if not rb.connect():
        return 2
    try:
        if a.parancs == "allapot":
            print()
            print(status_report(rb))
            return 0

        # leallit
        print()
        print(status_report(rb))
        print()
        if not a.igen:
            if input("Really shut down the robot? (yes/no) ").strip().lower() not in ("igen", "i", "y", "yes"):
                print("Cancelled — the robot keeps running.")
                return 1
        print()
        return 0 if shutdown(rb) else 3
    finally:
        rb.close()


# --------------------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------------------

def gui_main() -> int:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    BG, FG, ACC = "#1e1f26", "#e8e8ea", "#7aa2f7"
    root = tk.Tk()
    root.title("Robot panel")
    root.configure(bg=BG)
    root.geometry("620x520")
    root.minsize(520, 420)

    tk.Label(root, text="🤖  Robot panel", bg=BG, fg=ACC,
             font=("Segoe UI", 18, "bold")).pack(pady=(14, 2))
    tk.Label(root, text="status · graceful shutdown", bg=BG, fg="#9aa0ad",
             font=("Segoe UI", 10)).pack(pady=(0, 10))

    box = scrolledtext.ScrolledText(
        root, bg="#14151a", fg=FG, insertbackground=FG, relief="flat",
        font=("Consolas", 10), wrap="word", height=14, padx=10, pady=8,
    )
    box.pack(fill="both", expand=True, padx=14)
    box.configure(state="disabled")

    busy = threading.Event()

    def write(msg: str = "") -> None:
        def _w():
            box.configure(state="normal")
            box.insert("end", msg + "\n")
            box.see("end")
            box.configure(state="disabled")
        root.after(0, _w)

    def clear() -> None:
        def _c():
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(state="disabled")
        root.after(0, _c)

    def set_busy(on: bool) -> None:
        busy.set() if on else busy.clear()
        state = "disabled" if on else "normal"
        root.after(0, lambda: [b.configure(state=state) for b in (btn_stat, btn_off)])

    def worker(fn) -> None:
        """All network work on a separate thread — otherwise the window would freeze."""
        if busy.is_set():
            return
        set_busy(True)
        threading.Thread(target=lambda: _run(fn), daemon=True).start()

    def _run(fn) -> None:
        rb = Robot(log=write)
        try:
            if rb.connect():
                fn(rb)
        except Exception as e:
            write(f"❌ Unexpected error: {e}")
        finally:
            rb.close()
            write("")
            write(f"— {datetime.now().strftime('%H:%M:%S')} —")
            set_busy(False)

    def do_status(rb: Robot) -> None:
        write("")
        write(status_report(rb))

    def do_shutdown_checked() -> None:
        if not messagebox.askyesno(
            "Graceful shutdown",
            "Really shut down the robot?\n\n"
            "The shutdown puts the services to sleep cleanly, syncs the SD card,\n"
            "and powers the system off in software.\n\n"
            "Press the switch ONLY after the green ACT LED has gone dark for good!",
            icon="warning", default="no",
        ):
            return
        clear()
        worker(lambda rb: shutdown(rb, log=write))

    bar = tk.Frame(root, bg=BG)
    bar.pack(fill="x", padx=14, pady=14)

    def mkbtn(parent, text, cmd, bg, fg="#ffffff"):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg, relief="flat",
                         font=("Segoe UI", 12, "bold"), height=2, cursor="hand2",
                         borderwidth=0)

    btn_stat = mkbtn(bar, "🔋  Status + battery", lambda: (clear(), worker(do_status)), "#2c5aa0")
    btn_stat.pack(side="left", fill="x", expand=True, padx=(0, 7))
    btn_off = mkbtn(bar, "🌙  Graceful shutdown", do_shutdown_checked, "#a04040")
    btn_off.pack(side="left", fill="x", expand=True, padx=(7, 0))

    root.after(200, lambda: worker(do_status))
    root.mainloop()
    return 0


# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli_main(sys.argv[1:]))
    sys.exit(gui_main())
