# github_export — a cleaned, fail-closed copy of the GitHub package

_The exporter + filter build a cleaned copy of the part you want to publish. The "what may go out" decision lives in the manifest._

The file names are Hungarian: `exportalo.py` ("exportáló" = exporter), `szuro.py` ("szűrő" = filter), `tiltolista.json` ("tiltólista" = blocklist), `utolso_jelentes.txt` ("last report"), `teszt_exportalo.py` (the tests).

## Usage
```
python exportalo.py --manifest manifest.json --forras {HOME}/Desktop/Claude --cel <NEW folder> [--szaraz]
```
- **English aliases:** `--source` = `--forras`, `--target` = `--cel`, `--dry-run` = `--szaraz`, `--blocklist` = `--tiltolista`, `--report` = `--jelentes`, `--working-copy` = `--munkapeldany`.
- **`--szaraz` (dry run):** report only, no output is created. Always start with this.
- **`--munkapeldany` (working copy):** for manual generalization; writes the output even if names were found, but never if a secret was found. Not publishable.
- **Exit code:** 0 = clean (and, unless it is a dry run, the output is ready) · 2 = blocking finding, NO output · 1 = error (e.g. the target already exists).
- **Report:** `%LOCALAPPDATA%\github_export\utolso_jelentes.txt`, outside the repo. It is masked: you see the kind, the line and the first letter, not the matched word.

## What it does
1. Collects files using the manifest's `include`/`exclude` globs. ⚠️ fnmatch's `*` also matches `/`, so the real manifest must be precise. For example, `memoria-mcp/*.md` also picked up old generated files under `outputs/`.
2. **Path replacement** with the `csere` ("replace") list: regex → placeholder (`{HOME}`, `{PI_HOME}`).
3. **Personal comments are cut:** a `#`/`//` comment that contains a blocklisted word is removed, not rewritten. Docstrings, strings and markdown prose are NOT comments: a finding there blocks, and it has to be generalized by hand.
4. **Filtering:** secrets (keys, tokens, private keys, password lines, Twilio SID), e-mail, IPv4 (127.0.0.1 excepted), 8–12 digit identifiers (e.g. a chat_id), user paths, phone numbers, `.local` hostnames, and the blocklist. The blocklist **also matches words with suffixes** (Hungarian "Kukutyin**ban**" = "in Kukutyin").
5. **Binary files:** only extensions listed in `binaris_engedely` ("allowed binaries") may go out, and they are still listed in the report for manual review (EXIF).
6. The output is always a **fresh folder**, never an existing git history. `git init` and publishing are manual steps: review first, then the owner's approval.

## The blocklist
- Location: `%LOCALAPPDATA%\github_export\tiltolista.json`, format: `{"szavak": [...], "regexek": [...]}` ("words", "regexes").
- It is **personal data**, so it never goes into the repo, cloud storage or the assistant's memory.
- You may extend it; narrow it only with the owner's approval.
- Without it, the filter does not start.

## Tests
`python -X utf8 teszt_exportalo.py`: 11 cases with made-up names, all OK.
