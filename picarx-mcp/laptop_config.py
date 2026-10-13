"""laptop_config.py — connection settings for the laptop-side helper scripts
(robot_mond.py, robot_olvaso.py, szemlelo_laptop.py).

Lookup order for every value: environment variable first, then the matching key
in config.json next to this file (see config.example.json). A missing required
value stops the script with a clear message instead of guessing.

    PI_HOST     / "pi_host"   the robot's LAN address or hostname (required)
    PI_USER     / "pi_user"   ssh user on the robot (default: "pi")
    PI_KEY  / "ssh_key"   private key for ssh/scp (required); "{HOME}" and "~" are expanded
    PI_PROJECT_DIR / "pi_project_dir"  project folder ON THE ROBOT, relative to the robot
                              user's home or absolute (default: "picarx-mcp")
                              Keep the default: the robot-side scripts expect ~/picarx-mcp.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.path.join(_HERE, "config.json")


def _config():
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _expand(value):
    value = str(value).replace("{HOME}", os.path.expanduser("~"))
    return os.path.expanduser(value)


def get(key, env, default=None):
    value = os.environ.get(env) or _config().get(key) or default
    if not value:
        raise SystemExit(
            f"Missing setting '{key}': set the {env} environment variable or add "
            f"\"{key}\" to config.json (see config.example.json).")
    return _expand(value)


def pi_target():
    """'user@host' for ssh/scp."""
    return f"{get('pi_user', 'PI_USER', 'pi')}@{get('pi_host', 'PI_HOST')}"


def ssh_key():
    return get("ssh_key", "PI_KEY")


def pi_project_dir():
    """Project folder on the robot: absolute, or relative to the robot user's home (default picarx-mcp)."""
    return str(os.environ.get("PI_PROJECT_DIR") or _config().get("pi_project_dir") or "picarx-mcp")


def pi_remote_path(*parts):
    """Path inside the project folder on the robot, for ssh/scp targets (no expansion on the laptop side)."""
    base = pi_project_dir()
    return "/".join([base.rstrip("/"), *parts])
