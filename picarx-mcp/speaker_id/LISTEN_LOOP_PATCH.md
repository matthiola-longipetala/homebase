# How speaker identification is wired into the listen loop

This is already built into `../robot_listen_loop.py`; this note only explains the wiring.

## What it does
Each transcribed sentence gets a speaker label in front of it:
`[Alice]: ...`, `[valoszinuleg Alice]: ...` ("probably Alice"), `[ismeretlen hang]: ...`
("unknown voice"). The loop already writes the utterance to `/tmp/utt.wav` for the
transcriber, so that same file is sent for identification.
Switch off: `touch /tmp/speaker_off` (remove the file to switch back on).

The label strings are matched by `../robot_listen_loop.py` and `../robot_olvaso.py`; do not
translate or change them. `robot_olvaso.py` recognises enrolled names only if they are listed
in the `ROBOT_SPEAKERS` environment variable (comma-separated, e.g. `Alice,Bob`).

## Daemon-client integration (the one in use)
`install.sh` puts resemblyzer in a dedicated venv (`.spk-venv`), so the loop's own Python
cannot import it. Instead, `speaker_daemon.py` runs in that venv, loads the model once and
answers on the UNIX socket `/tmp/speaker.sock`. In the loop:

```python
SPEAKER_OFF = "/tmp/speaker_off"       # if it exists -> no speaker label
SPEAKER_SOCK = "/tmp/speaker.sock"     # speaker_daemon.py's socket

def speaker_label(wav_path):
    """Any error/timeout -> "" (no label); listening must never crash because of it."""
    if os.path.exists(SPEAKER_OFF) or not os.path.exists(SPEAKER_SOCK):
        return ""
    try:
        import socket as _s
        c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM); c.settimeout(6.0)
        c.connect(SPEAKER_SOCK)
        c.sendall(wav_path.encode("utf-8"))
        resp = c.recv(256).decode("utf-8", "replace").strip(); c.close()
        parts = resp.split("|")              # "name|name|score|[label]"
        return parts[-1] if parts else ""    # the "[label]" part
    except Exception as e:
        log("speaker_label error:", e); return ""
```

and just before a sentence is printed:

```python
spk = speaker_label("/tmp/utt.wav")
if spk:
    out = f"{spk} {out}"
print(out, flush=True)
```

If the daemon is not running or `/tmp/speaker_off` exists, `spk` is empty and the loop
behaves exactly as without speaker identification.

## In-process alternative
Only if resemblyzer is installed in the loop's own Python: at start-up do
`sys.path.insert(0, os.path.expanduser("~/picarx-mcp/speaker_id"))`, import
`speaker_id_lib`, call `load_encoder()` once, then label each WAV with
`identify(embed_wav(path), load_profiles(profiles_path), cfg["thresholds"])[0]`.

## Quick check
1. Start the daemon (`restart_spk.sh`): the log shows `[daemon] ready.` and the socket exists.
2. `identify.py <enrolment wav> --pipe` gives a sensible label.
3. `touch /tmp/speaker_off` -> unlabelled output; `rm /tmp/speaker_off` -> labelled again.
4. Try each enrolled speaker plus a stranger; tune thresholds in `speaker_config.json`
   (no code change needed).
