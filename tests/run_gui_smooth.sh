#!/bin/sh
# Usage : sh tests/run_gui_smooth.sh <blender.exe> [port] [lissage 0|1]
B="$1"; PORT="${2:-57421}"; SM="${3:-1}"
cd "$(dirname "$0")/.."
OUT=$(mktemp -d /tmp/mp_gs_XXXX); W=$(cygpath -w "$OUT")
export HOME="$W" USERPROFILE="$W"
("$B" --factory-startup --python tests/gui_smooth.py -- "$(cygpath -w "$PWD")" host "$PORT" "$W" "$SM" > "$OUT/host.log" 2>&1 &)
sleep 1
"$B" --factory-startup --python tests/gui_smooth.py -- "$(cygpath -w "$PWD")" guest "$PORT" "$W" "$SM" > "$OUT/guest.log" 2>&1
sleep 5
cat "$OUT"/smooth_*.json; echo
grep -iE "Traceback|Error" "$OUT"/*.log | head
