# image-mcp - image generation for Claude

A small MCP server that generates an image from a text prompt and saves it to disk.
It calls the image provider's API directly. Two providers are supported:

- **Cloudflare Workers AI** (`@cf/black-forest-labs/flux-1-schnell`) - has a free daily
  allowance and needs no credit card.
- **Google Gemini** (AI Studio API) - image output requires billing to be enabled on the
  Google project; on the free tier the image quota is 0.

## Requirements

- Python 3.10+
- `pip install mcp requests`

## Setup - Cloudflare (free, no credit card)

1. Create a free account at https://dash.cloudflare.com/sign-up
2. In the left menu open **AI -> Workers AI** and copy your **Account ID**.
3. Go to **My Profile -> API Tokens -> Create Token** and use the **"Workers AI"** template
   (or a custom token with *Account -> Workers AI -> Read*). Copy the token.
4. Put both values in your `config.json` (see below).

## Configuration

The config file holds API keys, so keep it **outside the repository and outside any
cloud-synced folder**, and point the `IMAGE_MCP_CONFIG` environment variable at it:

```
IMAGE_MCP_CONFIG=<secrets folder>/image-mcp/config.json
```

If `IMAGE_MCP_CONFIG` is not set, the server looks for `config.json` next to `server.py`
(make sure it is git-ignored).

```json
{
  "provider": "cloudflare",
  "default_out_dir": "<folder for generated images>",
  "cloudflare": { "account_id": "...", "api_token": "..." },
  "gemini_api_key": "AIza..."
}
```

- `provider`: `"cloudflare"` or `"gemini"`. If omitted, Cloudflare is used when its
  credentials are present, otherwise Gemini when a Gemini key is present.
- `default_out_dir`: where images are saved when no `out_path` is given.
  Default: `~/Desktop`.
- `cloudflare.model` (optional): override the Cloudflare model id.
- `gemini_api_key`: only needed for the Gemini provider. Get one at
  https://aistudio.google.com/apikey

Environment variables can be used instead of config values: `CF_ACCOUNT_ID`,
`CF_API_TOKEN`, `GEMINI_API_KEY`.

## Self-test (no MCP host needed)

```
python server.py --selftest "a cute robot waving hello"
```

On success it prints `OK -> <path of the saved image>`; on failure it prints `FAIL: <reason>`.

## Register with Claude Desktop

Once the self-test passes, add this to `claude_desktop_config.json` under `mcpServers`:

```json
"image-mcp": {
  "command": "python",
  "args": ["{REPO}/image-mcp/server.py"],
  "env": { "IMAGE_MCP_CONFIG": "<secrets folder>/image-mcp/config.json" }
}
```

Then restart the Claude app.

## Tool

`generate_image(prompt, out_path="", aspect_ratio="16:9", model="", reference_images=None, provider="")`

Returns the absolute path of the saved image.

- `prompt`: what to draw; be specific about style, composition and colors.
- `out_path`: absolute save path. Empty = `default_out_dir` with a timestamped file name.
- `provider`: override the configured provider for this call (`"cloudflare"` or `"gemini"`).
- `aspect_ratio` (`"1:1"`, `"16:9"`, `"9:16"`, `"4:3"`, `"3:4"`), `model` and
  `reference_images` (local image paths for style/context) are used by the Gemini
  provider only. Cloudflare FLUX-schnell has a fixed output size.

## Providers

- **cloudflare**: `@cf/black-forest-labs/flux-1-schnell`, free daily allowance.
- **gemini**: default model `gemini-3.1-flash-image`; if that model id is not available
  (HTTP 404), it falls back to `gemini-2.5-flash-image-preview`. Needs a billing-enabled
  Google project.

## Troubleshooting

- **"Cloudflare not configured"**: `account_id` / `api_token` are missing. Check the path
  in `IMAGE_MCP_CONFIG` and the JSON keys.
- **Gemini HTTP 429 with `limit: 0`**: the Google project is on the free tier, which does
  not include image output. Enable billing or use the Cloudflare provider.
- **Gemini HTTP 400 about `imageConfig` / `aspectRatio`**: the server retries automatically
  without the aspect ratio.
- **Tools not showing up in Claude**: run the self-test first, check the paths in
  `claude_desktop_config.json`, then restart the Claude app.
