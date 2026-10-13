"""
video-mcp v1.0 — video UNDERSTANDING via Google AI Studio Gemini, Zapier-free.

Why
---
An independent "second pair of eyes" that can watch a video and describe it
for Claude. It calls the Gemini API directly — same pattern as image-mcp
(one AI Studio key on the machine is enough).

What it can watch
-----------------
1. A YouTube URL  -> passed straight to Gemini as a file_data part (no download).
2. A LOCAL video file -> uploaded through the Gemini Files API, then analysed.
   (Small files < ~18 MB are sent inline; bigger ones go through Files API.)
3. Any other public direct video URL -> downloaded locally, then Files-API path.

Optional: clip a time range (start/end offset) and set the sampling FPS so you
can ask about just one part of a long video and keep the token cost down.

Config (VIDEO_MCP_CONFIG, or config.json next to this file)
--------------------------------------
{
  "gemini_api_key": "AIza...",                 // or env GEMINI_API_KEY
  "model": "gemini-2.5-flash"                   // optional override
}
If gemini_api_key is missing here, we fall back to the sibling image-mcp's
config.json key (so you really only need ONE AI Studio key on the machine).

CLI self-test:
  python server.py --selftest "https://www.youtube.com/watch?v=..."  "What happens in the video?"
  python server.py --selftest "<path>/clip.mp4"

Register in Claude Desktop (claude_desktop_config.json -> mcpServers) once it passes:
  "video-mcp": { "command": "python",
                 "args": ["{REPO}/video-mcp/server.py"],
                 "env": { "VIDEO_MCP_CONFIG": "<secrets folder>/video-mcp/config.json" } }
Then restart the Claude app.
"""

import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

# Keep the config (it holds API keys) OUTSIDE the repository and outside any cloud-synced folder:
# point VIDEO_MCP_CONFIG at it. Fallback: config.json next to this file (git-ignored).
CONFIG_PATH = os.environ.get("VIDEO_MCP_CONFIG") or str(Path(__file__).resolve().parent / "config.json")
# Sibling image-mcp config — reused only to borrow the Gemini key if ours lacks one.
IMAGE_MCP_CONFIG = os.environ.get("IMAGE_MCP_CONFIG") or str(
    Path(__file__).resolve().parent.parent / "image-mcp" / "config.json")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta/files"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_FALLBACK_MODEL = "gemini-flash-latest"

# Files API is required above this; below it we can send the bytes inline.
INLINE_LIMIT_BYTES = 18 * 1024 * 1024

DEFAULT_PROMPT = (
    "Watch this video and describe it in detail: what happens in it, who or what "
    "appears, what they do, what the setting and the mood are like. If there is "
    "speech or on-screen text, summarise its content. Be concrete and objective."
)

mcp = FastMCP("video-mcp")


# ======================================================================
# Config / key helpers
# ======================================================================

def _load_json(path: str) -> dict:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_cfg() -> dict:
    return _load_json(CONFIG_PATH)


def _gemini_key(cfg: dict) -> str:
    key = (
        os.environ.get("GEMINI_API_KEY")
        or cfg.get("gemini_api_key")
        or _load_json(IMAGE_MCP_CONFIG).get("gemini_api_key")
        or ""
    )
    if not key:
        raise ValueError(
            "No Gemini API key. Get one at https://aistudio.google.com/apikey and put it in "
            f'{CONFIG_PATH} as {{"gemini_api_key": "AIza..."}} (or set GEMINI_API_KEY). '
            "The sibling image-mcp key is reused automatically if present."
        )
    return key


def _model(cfg: dict, override: str) -> str:
    return override or cfg.get("model") or GEMINI_DEFAULT_MODEL


# ======================================================================
# Source classification + Gemini "part" builders
# ======================================================================

def _is_youtube(src: str) -> bool:
    s = src.lower()
    return "youtube.com/" in s or "youtu.be/" in s


def _is_http(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def _video_mime(path: Path) -> str:
    guess = mimetypes.guess_type(str(path))[0]
    if guess and guess.startswith("video/"):
        return guess
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mpeg": "video/mpeg",
        ".mpg": "video/mpeg", ".flv": "video/x-flv", ".wmv": "video/x-ms-wmv",
        ".3gp": "video/3gpp",
    }.get(path.suffix.lower(), "video/mp4")


def _video_metadata(start_offset: str, end_offset: str, fps) -> dict:
    """Build the optional videoMetadata block (clip range + sampling rate)."""
    meta = {}
    if start_offset:
        meta["startOffset"] = start_offset if start_offset.endswith("s") else f"{start_offset}s"
    if end_offset:
        meta["endOffset"] = end_offset if end_offset.endswith("s") else f"{end_offset}s"
    if fps:
        meta["fps"] = float(fps)
    return meta


def _download_to_temp(cfg: dict, url: str) -> Path:
    out_dir = Path(cfg.get("default_out_dir") or os.path.join(os.path.expanduser("~"), "Desktop"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1] or ".mp4"
    dest = out_dir / f"_video_mcp_dl_{int(time.time())}{ext}"
    with requests.get(url, stream=True, timeout=(60, 300)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Could not download video — HTTP {r.status_code}: {r.text[:300]}")
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


# ======================================================================
# Gemini Files API (resumable upload for local / large videos)
# ======================================================================

def _files_upload(key: str, path: Path) -> dict:
    """Resumable-upload a local file; return the active File resource dict."""
    size = path.stat().st_size
    mime = _video_mime(path)

    # 1) Start the resumable session.
    start = requests.post(
        f"{GEMINI_UPLOAD_BASE}?key={key}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": path.name}},
        timeout=(60, 120),
    )
    if start.status_code != 200:
        raise RuntimeError(f"Files API start failed — HTTP {start.status_code}: {start.text[:400]}")
    upload_url = start.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Files API start returned no upload URL.")

    # 2) Upload all bytes and finalize in one shot.
    with open(path, "rb") as f:
        up = requests.post(
            upload_url,
            headers={
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Length": str(size),
            },
            data=f,
            timeout=(60, 600),
        )
    if up.status_code != 200:
        raise RuntimeError(f"Files API upload failed — HTTP {up.status_code}: {up.text[:400]}")
    file_obj = up.json().get("file") or {}
    if not file_obj.get("uri"):
        raise RuntimeError(f"Files API returned no file URI: {str(up.json())[:400]}")
    return file_obj


def _files_wait_active(key: str, file_obj: dict, timeout_s: int = 300) -> dict:
    """Video uploads go through PROCESSING; poll until ACTIVE (or FAILED/timeout)."""
    name = file_obj.get("name")  # e.g. "files/abc123"
    state = file_obj.get("state")
    if state == "ACTIVE" or not name:
        return file_obj
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(f"{GEMINI_API_BASE}/{name}?key={key}", timeout=(60, 60))
        if r.status_code != 200:
            raise RuntimeError(f"Files API status check failed — HTTP {r.status_code}: {r.text[:300]}")
        info = r.json()
        state = info.get("state")
        if state == "ACTIVE":
            return info
        if state == "FAILED":
            raise RuntimeError(f"Gemini failed to process the uploaded video: {str(info)[:400]}")
        time.sleep(3)
    raise RuntimeError("Timed out waiting for Gemini to finish processing the uploaded video.")


# ======================================================================
# generateContent (streamed, so the long analysis keeps the socket warm)
# ======================================================================

def _gemini_generate(key, model, parts):
    body = {"contents": [{"parts": parts}]}
    url = f"{GEMINI_API_BASE}/models/{model}:streamGenerateContent?alt=sse&key={key}"
    return requests.post(url, json=body, timeout=(60, 600), stream=True)


def _collect_stream(resp) -> str:
    # Gemini's SSE body is UTF-8, but it's served as text/event-stream with no
    # charset, so requests defaults to ISO-8859-1 and mangles non-ASCII (e.g.
    # "videó" -> "videÃ³"). Force UTF-8 before decoding the stream.
    resp.encoding = "utf-8"
    chunks = []
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
                if part.get("text"):
                    chunks.append(part["text"])
    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("Gemini returned no text for this video.")
    return text


# ======================================================================
# Core understand routine
# ======================================================================

def _understand(source, prompt="", model="", start_offset="", end_offset="",
                fps=None, keep_download=False) -> str:
    cfg = _load_cfg()
    key = _gemini_key(cfg)
    mdl = _model(cfg, model)
    prompt = prompt or DEFAULT_PROMPT
    vmeta = _video_metadata(start_offset, end_offset, fps)

    video_part = None
    cleanup = None  # temp file to delete afterwards

    if _is_youtube(source):
        video_part = {"fileData": {"fileUri": source}}
    else:
        local = Path(source)
        if _is_http(source):
            local = _download_to_temp(cfg, source)
            cleanup = local
        if not local.is_file():
            raise FileNotFoundError(f"Video not found: {source}")

        size = local.stat().st_size
        if size <= INLINE_LIMIT_BYTES:
            video_part = {"inlineData": {
                "mimeType": _video_mime(local),
                "data": base64.b64encode(local.read_bytes()).decode("ascii"),
            }}
        else:
            file_obj = _files_wait_active(key, _files_upload(key, local))
            video_part = {"fileData": {
                "mimeType": file_obj.get("mimeType") or _video_mime(local),
                "fileUri": file_obj["uri"],
            }}

    if vmeta:
        video_part["videoMetadata"] = vmeta
    parts = [video_part, {"text": prompt}]

    try:
        models = [mdl] + ([GEMINI_FALLBACK_MODEL] if mdl != GEMINI_FALLBACK_MODEL else [])
        last_err = None
        for m in models:
            r = _gemini_generate(key, m, parts)
            if r.status_code == 200:
                return _collect_stream(r)
            last_err = f"HTTP {r.status_code}: {r.text[:600]}"
            if r.status_code == 404:  # model id not available -> try fallback
                continue
            raise RuntimeError(f"Gemini API error — {last_err}")
        raise RuntimeError(f"Video understanding failed. Last error: {last_err}")
    finally:
        if cleanup and not keep_download:
            try:
                cleanup.unlink()
            except OSError:
                pass


# ======================================================================
# MCP tools
# ======================================================================

@mcp.tool()
def understand_youtube_video(url: str, prompt: str = "", model: str = "",
                             start_offset: str = "", end_offset: str = "",
                             fps: float = None) -> str:
    """Watch a YouTube video with Gemini and return a text description/answer.

    No download — the URL is handed straight to Gemini. Works for public videos.

    Args:
        url: A youtube.com/watch?v=... or youtu.be/... link (public/unlisted).
        prompt: What to look for / question to answer. Empty = a detailed general description.
        model: Override the Gemini model id. Empty = config/default (gemini-2.5-flash).
        start_offset: Clip start, e.g. "90" or "90s" (seconds). Empty = from the beginning.
        end_offset: Clip end, e.g. "150" or "150s". Empty = to the end.
        fps: Sampling frames-per-second (e.g. 1 for slow content, higher for fast action).

    Returns:
        Gemini's textual analysis of the video.
    """
    if not _is_youtube(url):
        raise ValueError("This tool is for YouTube links. Use understand_video for files/other URLs.")
    return _understand(url, prompt, model, start_offset, end_offset, fps)


@mcp.tool()
def understand_video(source: str, prompt: str = "", model: str = "",
                     start_offset: str = "", end_offset: str = "",
                     fps: float = None) -> str:
    """Watch a LOCAL video file (or a direct video URL, or YouTube) and return a description.

    Local files <~18 MB are sent inline; larger ones are uploaded via the Gemini
    Files API automatically. Direct (non-YouTube) URLs are downloaded to a temp
    file first, then deleted.

    Args:
        source: Absolute path to a video file, OR a direct video URL, OR a YouTube link.
        prompt: What to look for / question to answer. Empty = a detailed general description.
        model: Override the Gemini model id. Empty = config/default (gemini-2.5-flash).
        start_offset: Clip start in seconds, e.g. "30" or "30s". Empty = from the beginning.
        end_offset: Clip end in seconds, e.g. "75" or "75s". Empty = to the end.
        fps: Sampling frames-per-second.

    Returns:
        Gemini's textual analysis of the video.
    """
    return _understand(source, prompt, model, start_offset, end_offset, fps)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        src = sys.argv[2] if len(sys.argv) >= 3 else "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        ask = sys.argv[3] if len(sys.argv) >= 4 else ""
        try:
            print("OK ->\n" + _understand(src, ask))
        except Exception as exc:  # noqa: BLE001 - selftest prints the message verbatim
            print(f"FAIL: {exc}")
            sys.exit(1)
    else:
        mcp.run()
