"""Configuration loader for phonecall-mcp."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Keys belong OUTSIDE the repository: PHONECALL_MCP_CONFIG points to your config.json (e.g. in your secrets
# folder), and the .env next to that file is loaded too. Fallback: config.json / .env next to this file.
CONFIG_PATH = Path(os.environ.get("PHONECALL_MCP_CONFIG") or (Path(__file__).parent / "config.json"))
load_dotenv(CONFIG_PATH.parent / ".env")


def load_config() -> dict:
    """Load config.json and merge with environment variables."""
    if not CONFIG_PATH.exists():
        print(
            "ERROR: config.json not found! Copy config.example.json to config.json and fill it in.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    # Inject env vars
    cfg["twilio_account_sid"] = os.environ.get("TWILIO_ACCOUNT_SID", "")
    cfg["twilio_auth_token"] = os.environ.get("TWILIO_AUTH_TOKEN", "")
    cfg["twilio_phone_number"] = os.environ.get("TWILIO_PHONE_NUMBER", "")
    cfg["elevenlabs_api_key"] = os.environ.get("ELEVENLABS_API_KEY", "")
    cfg["ngrok_url"] = os.environ.get("NGROK_URL", "")

    # Validate required keys
    missing = []
    for key in ["twilio_account_sid", "twilio_auth_token", "twilio_phone_number",
                "elevenlabs_api_key", "ngrok_url"]:
        if not cfg.get(key):
            missing.append(key.upper())
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in the values.", file=sys.stderr)
        sys.exit(1)

    return cfg


config = load_config()
