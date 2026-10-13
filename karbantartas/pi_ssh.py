#!/usr/bin/env python3
"""pi_ssh.py — SSH/SFTP to the PiCar robot with PARAMIKO, WITHOUT ssh.exe.

WHY THIS EXISTS. When started from the Windows-MCP PowerShell context, `ssh.exe` **dies silently**
(exit 255, empty stdout AND empty stderr, even with `-v`, after about 46 ms). The earlier workaround was
the `bridge.py` HTTP bridge running on the Pi, but THAT also has to be started somehow — so the workaround
had the same precondition as the main path. After a boot the bridge is not running, so the chat assistant
was BLOCKED after every robot power-on until someone (a person, or another session with working ssh)
started it.

paramiko speaks SSH INSIDE the Python process, so the `ssh.exe` problem does not affect it.
Connection settings come from the PI_HOST, PI_USER, PI_KEY and PI_PROJECT_DIR environment variables.

Usage:
    py -3 -X utf8 pi_ssh.py run  "uname -a; ls picarx-mcp | head"
    py -3 -X utf8 pi_ssh.py put  <local_file> <remote_path>
    py -3 -X utf8 pi_ssh.py get  <remote_file> <local_path>
    py -3 -X utf8 pi_ssh.py ping
Exit code: the remote command's rc (run), otherwise 0/1.
"""
import sys
import os

HOST = os.environ.get("PI_HOST", "")   # the robot's IP or hostname; required
USER = os.environ.get("PI_USER", "pi")
KEY = os.environ.get("PI_KEY", os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519"))
CWD = os.environ.get("PI_PROJECT_DIR", "picarx-mcp")   # relative = under the Pi user's home


def kapcsolat():
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = None
    for cls in ("Ed25519Key", "RSAKey", "ECDSAKey"):
        try:
            pkey = getattr(paramiko, cls).from_private_key_file(KEY)
            break
        except Exception:
            continue
    if pkey is None:
        raise RuntimeError(f"cannot read the key: {KEY}")
    c.connect(HOST, username=USER, pkey=pkey, timeout=20,
              banner_timeout=20, auth_timeout=20)
    return c


def fut(c, cmd, timeout=180):
    # set the working directory explicitly so relative calls (python3 robot_say.py) work
    _in, out, err = c.exec_command(f"cd {CWD} 2>/dev/null; {cmd}", timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    if o:
        print(o, end="" if o.endswith("\n") else "\n")
    if e:
        print("--- stderr ---")
        print(e, end="" if e.endswith("\n") else "\n")
    print(f"[rc={rc}]")
    return rc


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mod = sys.argv[1]
    if not HOST:
        print("ERROR: set PI_HOST to the robot's IP address or hostname")
        return 2
    try:
        c = kapcsolat()
    except Exception as ex:
        print(f"ERROR: could not connect to {USER}@{HOST}: {ex}")
        return 1
    try:
        if mod == "ping":
            return fut(c, "echo ALIVE; hostname; uptime -p")
        if mod == "run":
            if len(sys.argv) < 3:
                print("ERROR: missing command")
                return 2
            return fut(c, " ".join(sys.argv[2:]))
        if mod in ("put", "get"):
            if len(sys.argv) < 4:
                print("ERROR: put/get <source> <target>")
                return 2
            s = c.open_sftp()
            try:
                if mod == "put":
                    s.put(sys.argv[2], sys.argv[3])
                    m = s.stat(sys.argv[3])
                    print(f"OK: uploaded -> {sys.argv[3]} ({m.st_size} bytes)")
                else:
                    s.get(sys.argv[2], sys.argv[3])
                    print(f"OK: downloaded -> {sys.argv[3]} "
                          f"({os.path.getsize(sys.argv[3])} bytes)")
            finally:
                s.close()
            return 0
        print(f"unknown mode: {mod}")
        return 2
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
