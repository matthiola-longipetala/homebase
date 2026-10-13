# video-mcp - video understanding for Claude with Gemini

A small MCP server that gives Claude an independent "second pair of eyes": Google Gemini
watches a video and returns a text description or answers your question about it.
It calls the Google AI Studio API directly (same pattern as `image-mcp`).

Only one AI Studio key is needed. If video-mcp's own config has no key, it reuses the
Gemini key from the image-mcp config.

## What it can watch

- **YouTube URL** - passed straight to Gemini, no download (public or unlisted videos).
- **Local video file** - files up to ~18 MB are sent inline; larger files are uploaded
  through the Gemini Files API automatically.
- **Other direct video URL** - downloaded first (into `default_out_dir`), analysed,
  then deleted.

Optional: a **time range** (`start_offset` / `end_offset`, in seconds) and the frame
**sampling rate** (`fps`), so you can ask about just part of a long video at a lower cost.

## Requirements

- Python 3.10+
- `pip install mcp requests`

## Configuration

The config file holds an API key, so keep it **outside the repository and outside any
cloud-synced folder**, and point the `VIDEO_MCP_CONFIG` environment variable at it:

```
VIDEO_MCP_CONFIG=<secrets folder>/video-mcp/config.json
```

If `VIDEO_MCP_CONFIG` is not set, the server looks for `config.json` next to `server.py`
(make sure it is git-ignored).

```json
{
  "gemini_api_key": "AIza...",
  "model": "gemini-2.5-flash",
  "default_out_dir": "<folder for temporary downloads>"
}
```

All keys are optional:

- `gemini_api_key`: get one at https://aistudio.google.com/apikey. The key is looked up
  in this order: the `GEMINI_API_KEY` environment variable, this config, then the
  image-mcp config (the file named by `IMAGE_MCP_CONFIG`, or `../image-mcp/config.json`).
- `model`: Gemini model id. Default: `gemini-2.5-flash`.
- `default_out_dir`: where direct-URL videos are downloaded before analysis.
  Default: `~/Desktop`.

## Self-test (no MCP host needed)

```
python server.py --selftest "https://www.youtube.com/watch?v=aqz-KE-bpKQ" "What happens in the video?"
python server.py --selftest "<path>/clip.mp4"
```

On success it prints `OK ->` followed by Gemini's analysis; on failure it prints
`FAIL: <reason>`.

## Register with Claude Desktop

Once the self-test passes, add this to `claude_desktop_config.json` under `mcpServers`:

```json
"video-mcp": {
  "command": "python",
  "args": ["{REPO}/video-mcp/server.py"],
  "env": { "VIDEO_MCP_CONFIG": "<secrets folder>/video-mcp/config.json" }
}
```

If you want to borrow the key from image-mcp and the two are not side by side in the
same repository, also add `"IMAGE_MCP_CONFIG": "<secrets folder>/image-mcp/config.json"`
to `env`. Then restart the Claude app.

## Tools

- `understand_youtube_video(url, prompt="", model="", start_offset="", end_offset="", fps=None)`:
  analyses a YouTube link.
- `understand_video(source, prompt="", model="", start_offset="", end_offset="", fps=None)`:
  analyses a local file, a direct video URL or a YouTube link (detected automatically).

Parameters:

- `prompt`: what to look for or a question to answer. Empty = a detailed general
  description of the video (what happens, who appears, setting, speech and on-screen text).
- `model`: override the Gemini model id for this call.
- `start_offset` / `end_offset`: clip range in seconds, e.g. `"90"` or `"90s"`.
- `fps`: frame sampling rate (e.g. `1` for slow content, higher for fast action).

## Notes and troubleshooting

- If the chosen model id is not available (HTTP 404), the server falls back to
  `gemini-flash-latest`.
- YouTube analysis only works for public or unlisted videos, not private ones.
- Token cost grows with video length; for long videos set `start_offset` / `end_offset`
  and a low `fps`.
- **"No Gemini API key"**: none of the key sources above has a key. Check the paths in
  `VIDEO_MCP_CONFIG` / `IMAGE_MCP_CONFIG`.
- **"Timed out waiting for Gemini to finish processing"**: large uploads go through a
  processing step on Google's side; try a shorter clip or retry later.
- **Tools not showing up in Claude**: run the self-test first, check the paths in
  `claude_desktop_config.json`, then restart the Claude app.
