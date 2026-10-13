# emlek_index — associative recall (optional)

*"emlék" = memory. The file names are Hungarian; everything inside is in English.*

When a message arrives (Telegram, the robot's ear, the family group), the watcher can print one extra line right after it:

```
[MEMORY] 2026-01-05 (Day Claude) 2026-01-05_session_1:3 — The cat fell asleep on the warm windowsill again… | …
```

These are up to three memories from your journals and memory files that the message brings to mind. Each one comes with its date, whose memory it is, and file:line. When nothing stands out, there is no line at all. The point is the way a friend's memory works: you don't search, it comes to you.

It runs fully on your machine. A small multilingual embedding model (`intfloat/multilingual-e5-small`, 384 dimensions) builds the index, and a local server answers on 127.0.0.1 only. Nothing goes out, and the queried sentences are not logged.

## Files

| File | What it does |
|---|---|
| `emlek_kozos.py` | shared: sources, chunking, author tag, secret filter, config |
| `emlek_epito.py` | builds the index (idempotent; unchanged chunks are not embedded again) |
| `emlek.py` | the query: scoring, noise gate, one-line output; also a command-line tool |
| `emlek_szerver.py` | local HTTP server (the model loads once; a query then takes ~0.1 s) |
| `emlek_kliens.py` | stdlib-only client the watchers import; never raises, waits at most 3 s |
| `emlek_onnx_export.py` | optional one-off: exports the model to ONNX (half the memory) |
| `emlek_config.example.json` | config template: sources, index folder, author rules, stopwords |

## Install

1. Create a venv for it **outside any cloud-synced folder** (torch is large):
   ```
   python -m venv ~/.emlek_venv
   ~/.emlek_venv/Scripts/python -m pip install -r requirements.txt     # Windows; on Linux/macOS: ~/.emlek_venv/bin/python
   ```
2. Copy `emlek_config.example.json` to `emlek_config.json` and fill it in. Every key is optional. Without a config, the memory folder is `CLAUDE_MEMORY_DIR` (or `../memoria`), and the sources are its `naplo/`, `kontextus/` and `projektek/` folders. Keep `index_dir` local too, not in a synced folder.
3. Build the index, then try a query — always with the venv's Python (`~/.emlek_venv/Scripts/python` on Windows, `~/.emlek_venv/bin/python` elsewhere; written `VPY` below):
   ```
   VPY -X utf8 emlek_epito.py
   VPY -X utf8 emlek.py --ma-is --no-gate "the taste of gummy bears"
   ```
   (`--ma-is` = "today too": today's files are skipped by default; `--no-gate` shows hits below the noise gate. Drop both once the memory has a few days in it.)
4. Optional, to halve the memory use: `VPY emlek_onnx_export.py`. It writes `~/.cache/emlek_onnx/` and checks the vectors itself; expect fp32 ≈ 1.0000 and int8 ≈ 0.99. The encoder is then picked by `EMLEK_KODOLO`: `onnx-int8` (default), `onnx` or `st` (sentence-transformers). If the ONNX files are missing, it falls back to `st`.
5. Start the server as a standalone background process, then point the watchers at this folder:
   ```
   VPY -X utf8 emlek_szerver.py               # 127.0.0.1:8770
   ```
   Set `EMLEK_INDEX_DIR={REPO}/emlek_index` (the CODE folder, so the watchers can import the stdlib-only client) for the watchers; the index DATA folder is `index_dir` in the config or `EMLEK_INDEX_DATA`, and the config file itself can be moved with `EMLEK_CONFIG`. `monitor_wake_v3_db.py` and `csoport_tars_figyelo.py` then print the `[MEMORY]` line; `--no-recall` switches it off per watcher.
6. Rebuild nightly (Task Scheduler / cron): `VPY -X utf8 emlek_epito.py --csendes`. The server notices the new index by itself, so no restart is needed.

## How a line is chosen

- **Unit:** a paragraph. In a long list, one list item. An over-long block is cut at sentence boundaries (~1200 characters). The heading above a block goes into the embedding, not the output.
- **Score:** cosine similarity + 0.06 × the idf weight of the *rare word stems* the message shares with the chunk. A stem is the first 5 letters of a 4+ letter word, which cuts off most suffixes. The lexical part matters for names: pure meaning-based search under-weights them.
- **Noise gate:** a line only comes when (a) the message has a content word ("Okay, thanks" doesn't), AND (b) the best hit stands out from the message's own score distribution (best cosine − 51st best ≥ 0.03) OR shares a rare stem with it (idf ≥ `rare_idf`, default 4.0).
- **At most one hit per file**, and **today's chunks are left out**: today's journal only echoes the conversation happening right now.
- **Excluded:** handover files (`atado_*.md`), backups, the memory index file. A chunk that looks like a key, token or password is dropped.

## Tuning (do this on your own memory)

`python emlek.py --json --no-gate "…"` shows the scores and the shared stems. Try 10–20 sentences, some that should recall something and some that shouldn't ("Good morning!"), and adjust `rare_idf` in the config.

**Watch the drift:** idf = ln(N/df) grows with the index size N, so a fixed `rare_idf` lets more lines through as your memory grows. Re-check it when the chunk count (in `info.json`) has grown a lot.

Put names that appear everywhere (yours, your Claude's) under `stopwords`. A word that is in every second chunk is not evidence.

## Two Claudes: whose memory is it?

With two Claudes sharing one memory, the line says who lived it (`session_authors`, `journal_rules`, `frontmatter_rules` in the config). The first matching rule wins; the rest is `default_author` ("shared"). A memory of something the other one did can still be yours to know, but not yours to have experienced.

## Privacy

Confidential memory files are indexed like any other. Whether to *say* what came up is a matter of tact, and the line is for Claude, not an announcement. If something must never surface, keep it out of the source folders or add an `exclude_patterns` rule.
