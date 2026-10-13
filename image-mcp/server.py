"""
image-mcp v1.1 — FREE image generation, Zapier-free, provider-switchable.

Background
----------
By mid-2026 there is no keyless free image generation left:
- Zapier "Generate Image" / `google_ai_studio_gemini_generate_image` -> route to Imagen,
  paid ("Imagen 3 is only available on paid plans").
- Google AI Studio DIRECT API -> image output is NOT in the free tier either: a valid key
  still gets HTTP 429 `generate_content_free_tier_requests, limit: 0`.
- Pollinations.ai -> HTTP 402 (paid).
- Public HF FLUX Spaces (ZeroGPU) -> require login.

So we use a provider that has a real free tier with a free token.

PRIMARY provider: Cloudflare Workers AI  (@cf/black-forest-labs/flux-1-schnell)
  - Generous free daily allocation, no credit card needed.
  - Needs: free Cloudflare account -> account_id + an API token with "Workers AI" read.
SECONDARY provider: Google AI Studio Gemini (kept for if billing is ever enabled).

Config (IMAGE_MCP_CONFIG, or config.json next to this file)
--------------------------------------
{
  "provider": "cloudflare",                  // "cloudflare" | "gemini"
  "default_out_dir": "<folder for generated images>",
  "cloudflare": { "account_id": "...", "api_token": "..." },
  "gemini_api_key": "AIza..."                // only used when provider == "gemini"
}
Env-var fallbacks: CF_ACCOUNT_ID, CF_API_TOKEN, GEMINI_API_KEY.

CLI self-test:
  python server.py --selftest "a cute robot waving hello"

Register in Claude Desktop (claude_desktop_config.json -> mcpServers) once selftest passes:
  "image-mcp": { "command": "python",
                 "args": ["{REPO}/image-mcp/server.py"],
                 "env": { "IMAGE_MCP_CONFIG": "<secrets folder>/image-mcp/config.json" } }
Then restart the Claude app.
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

# Keep the config (it holds API keys) OUTSIDE the repository and outside any cloud-synced folder:
# point IMAGE_MCP_CONFIG at it. Fallback: config.json next to this file (git-ignored).
CONFIG_PATH = os.environ.get("IMAGE_MCP_CONFIG") or str(Path(__file__).resolve().parent / "config.json")

# --- Gemini (secondary; needs billing for image output) ---
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_DEFAULT_MODEL = "gemini-3.1-flash-image"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-image-preview"

# --- Cloudflare Workers AI (the default provider) ---
CF_DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"

mcp = FastMCP("image-mcp")


# ======================================================================
# Config helpers
# ======================================================================

def _load_cfg() -> dict:
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _auto_provider(cfg: dict) -> str:
    cf = cfg.get("cloudflare") or {}
    if (cf.get("account_id") or os.environ.get("CF_ACCOUNT_ID")) and \
       (cf.get("api_token") or os.environ.get("CF_API_TOKEN")):
        return "cloudflare"
    if cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "cloudflare"


def _default_out_dir(cfg: dict) -> Path:
    d = cfg.get("default_out_dir") or os.path.join(os.path.expanduser("~"), "Desktop")
    return Path(d)


def _ext_for_mime(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")


def _save(cfg: dict, out_path: str, img: bytes, mime: str) -> str:
    op = Path(out_path) if out_path else (
        _default_out_dir(cfg) / f"gen_{int(time.time())}{_ext_for_mime(mime)}"
    )
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_bytes(img)
    return str(op)


# ======================================================================
# Cloudflare Workers AI (primary)
# ======================================================================

def _generate_cloudflare(cfg, prompt, out_path, steps=4, seed=None) -> str:
    cf = cfg.get("cloudflare") or {}
    acct = cf.get("account_id") or os.environ.get("CF_ACCOUNT_ID")
    token = cf.get("api_token") or os.environ.get("CF_API_TOKEN")
    model = cf.get("model") or CF_DEFAULT_MODEL
    if not acct or not token:
        raise ValueError(
            "Cloudflare not configured. Free setup: dash.cloudflare.com -> create account "
            "-> 'Workers AI' (copy Account ID) -> My Profile > API Tokens > Create Token "
            "with the 'Workers AI' template. Put them in "
            f'{CONFIG_PATH} under "cloudflare": {{"account_id": "...", "api_token": "..."}}.'
        )
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    body = {"prompt": prompt, "steps": max(1, min(int(steps), 8))}
    if seed is not None:
        body["seed"] = seed
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Cloudflare AI error — HTTP {r.status_code}: {r.text[:600]}")

    ctype = r.headers.get("Content-Type", "")
    if "application/json" in ctype:
        data = r.json()
        if not data.get("success", True):
            raise RuntimeError(f"Cloudflare AI returned errors: {str(data.get('errors'))[:400]}")
        result = data.get("result") or {}
        b64 = result.get("image")
        if not b64:
            raise RuntimeError(f"No image in Cloudflare response: {str(data)[:400]}")
        return _save(cfg, out_path, base64.b64decode(b64), "image/jpeg")
    # Some models stream raw image bytes instead of JSON.
    mime = ctype.split(";")[0] if ctype.startswith("image/") else "image/png"
    return _save(cfg, out_path, r.content, mime)


# ======================================================================
# Google AI Studio Gemini (secondary; needs billing as of 2026-06)
# ======================================================================

def _gemini_key(cfg: dict) -> str:
    key = os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key") or ""
    if not key:
        raise ValueError(
            "No Gemini API key. Get one at https://aistudio.google.com/apikey and put it in "
            f'{CONFIG_PATH} as {{"gemini_api_key": "AIza..."}}. NOTE: image output needs '
            "billing enabled on the Google project (free tier limit is 0)."
        )
    return key


def _gemini_extract_stream(resp):
    """Parse the SSE stream from streamGenerateContent; return (image_bytes, mime).

    Streaming keeps the TCP connection active during the (slow) image generation,
    so a router/middlebox can't mistake the long request for an idle connection
    and drop it — which was causing connection resets / read timeouts with the
    plain (non-streaming) generateContent call.
    """
    text_chunks = []
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        for c in obj.get("candidates") or []:
            for part in (c.get("content", {}) or {}).get("parts", []) or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    return base64.b64decode(inline["data"]), mime
                if part.get("text"):
                    text_chunks.append(part["text"])
    raise RuntimeError("No image part in streamed response. Model said: "
                       + (" ".join(text_chunks).strip() or "<empty>"))


def _gemini_call(key, model, prompt, aspect_ratio, ref_parts, with_image_config):
    body = {
        "contents": [{"parts": [{"text": prompt}] + ref_parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    if with_image_config and aspect_ratio:
        body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}
    # Streamed (SSE) so the connection stays warm while the image is generated.
    url = f"{GEMINI_API_BASE}/{model}:streamGenerateContent?alt=sse&key={key}"
    # timeout = (connect, read-between-chunks); SSE sends data periodically.
    return requests.post(url, json=body, timeout=(15, 180), stream=True)


def _generate_gemini(cfg, prompt, out_path, aspect_ratio, model, reference_images) -> str:
    key = _gemini_key(cfg)
    ref_parts = []
    mime_by_ext = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp"}
    for p in (reference_images or []):
        rp = Path(p)
        if not rp.is_file():
            raise FileNotFoundError(f"Reference image not found: {p}")
        ref_parts.append({"inlineData": {
            "mimeType": mime_by_ext.get(rp.suffix.lower(), "image/png"),
            "data": base64.b64encode(rp.read_bytes()).decode("ascii"),
        }})

    models = [model] + ([GEMINI_FALLBACK_MODEL] if model != GEMINI_FALLBACK_MODEL else [])
    last_err = None
    for m in models:
        for with_ic in (True, False):
            r = _gemini_call(key, m, prompt, aspect_ratio, ref_parts, with_ic)
            if r.status_code == 200:
                img, mime = _gemini_extract_stream(r)
                return _save(cfg, out_path, img, mime)
            txt = r.text[:600]  # consumes the (small) error body
            last_err = f"HTTP {r.status_code}: {txt}"
            if r.status_code == 404:
                break
            if r.status_code == 400 and any(k in txt for k in ("imageConfig", "aspectRatio", "Unknown name")):
                continue
            raise RuntimeError(f"Gemini API error — {last_err}")
    raise RuntimeError(f"Gemini image generation failed. Last error: {last_err}")


# ======================================================================
# Dispatcher + MCP tool
# ======================================================================

def _generate(prompt, out_path="", aspect_ratio="16:9", model="",
              reference_images=None, provider="") -> str:
    cfg = _load_cfg()
    prov = (provider or cfg.get("provider") or _auto_provider(cfg)).lower()
    if prov == "cloudflare":
        return _generate_cloudflare(cfg, prompt, out_path)
    if prov == "gemini":
        return _generate_gemini(cfg, prompt, out_path, aspect_ratio,
                                model or GEMINI_DEFAULT_MODEL, reference_images)
    raise ValueError(f"Unknown provider '{prov}'. Use 'cloudflare' or 'gemini'.")


@mcp.tool()
def generate_image(prompt: str, out_path: str = "", aspect_ratio: str = "16:9",
                   model: str = "", reference_images: list = None,
                   provider: str = "") -> str:
    """Generate an image and save it to disk; returns the saved file path.

    Uses the provider from config.json ("cloudflare" by default; "gemini" available
    if billing is enabled). No Zapier involved.

    Args:
        prompt: What to draw — be specific about style, composition, colors, details.
        out_path: Absolute save path. Empty = default_out_dir with a timestamped name.
        aspect_ratio: Only used by the gemini provider ("1:1","16:9","9:16","4:3","3:4");
            ignored by cloudflare flux-schnell (fixed output).
        model: Override the model id (provider-specific). Empty = provider default.
        reference_images: Optional local image paths for style/context (gemini only).
        provider: Override the configured provider for this call ("cloudflare"/"gemini").

    Returns:
        The absolute path to the saved image file.
    """
    return _generate(prompt, out_path, aspect_ratio, model, reference_images, provider)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        test_prompt = (
            sys.argv[2] if len(sys.argv) >= 3
            else "a cute friendly rover robot waving hello, flat vector illustration"
        )
        try:
            print(f"OK -> {_generate(test_prompt)}")
        except Exception as exc:  # noqa: BLE001 - selftest prints the message verbatim
            print(f"FAIL: {exc}")
            sys.exit(1)
    else:
        mcp.run()
