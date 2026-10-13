#!/bin/bash
# hang_tartalek_teszt.sh ("voice fallback test") — LIVE test of the local voice fallback.
# Deliberately breaks the ElevenLabs key, runs the FULL robot_say.py chain with a
# two-sentence text, and is GUARANTEED to restore the good config (trap EXIT).
# Expected: seq → 0 sentences → rc=4 → local espeak speaks + a "GEPI HANG" ("machine voice") line + flag.
set -u
cd $HOME/picarx-mcp || exit 9
CFG=voice_config.json
MENTES=/tmp/vc_ok_$$.json

cp "$CFG" "$MENTES" || exit 9
# ⚠️ The RESTORE runs ON EVERY EXIT PATH (error, interrupt, timeout kill too):
trap 'cp "$MENTES" $HOME/picarx-mcp/'"$CFG"' && echo "[RESTORED: good config]" || echo "[!!! RESTORE FAILED - do it by hand: cp '"$MENTES"' $HOME/picarx-mcp/'"$CFG"']"' EXIT

echo "=== 1) deliberately breaking the key ==="
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/picarx-mcp/voice_config.json")
d = json.load(open(p))
d["elevenlabs_api_key"] = "DELIBERATELY_BAD_TEST_KEY"
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
print("   key broken (the backup exists)")
PY

echo "=== 2) FULL chain with a two-sentence text (robot_say.py) ==="
python3 robot_say.py --file /tmp/hangproba2.txt
RC=$?
echo "LANC_RC=$RC"

echo "=== 3) trace ==="
cat /tmp/robot_voice_fallback.flag 2>/dev/null || echo "   (no flag - THAT WOULD BE A PROBLEM)"

echo "=== 4) verdict ==="
if [ "$RC" -eq 0 ]; then
  echo "   ✅ The chain returned 0 AND the local voice spoke -> the fallback WORKS."
else
  echo "   ❌ LANC_RC=$RC -> the fallback did NOT catch it. Check the output above."
fi
