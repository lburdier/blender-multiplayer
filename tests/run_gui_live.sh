#!/bin/sh
# Usage : sh tests/run_gui_live.sh <blender.exe> [port]
B="$1"; PORT="${2:-57470}"
cd "$(dirname "$0")/.."
OUT=$(mktemp -d /tmp/mp_gl_XXXX); W=$(cygpath -w "$OUT")
export HOME="$W" USERPROFILE="$W"
("$B" --factory-startup --python tests/gui_live.py -- "$(cygpath -w "$PWD")" host "$PORT" "$W" > "$OUT/host.log" 2>&1 &)
sleep 1
"$B" --factory-startup --python tests/gui_live.py -- "$(cygpath -w "$PWD")" guest "$PORT" "$W" > "$OUT/guest.log" 2>&1
sleep 4
echo "Dossier : $W"
cat "$OUT"/live_gui.json; echo
grep -iE "Traceback|Error" "$OUT"/*.log | head
