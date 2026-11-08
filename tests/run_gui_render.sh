#!/bin/sh
# Usage : sh tests/run_gui_render.sh <blender.exe> [EEVEE|CYCLES] [port]
B="$1"; ENG="${2:-EEVEE}"; PORT="${3:-57401}"
cd "$(dirname "$0")/.."
OUT=$(mktemp -d /tmp/mp_gr_XXXX); W=$(cygpath -w "$OUT")
export HOME="$W" USERPROFILE="$W"
("$B" --factory-startup --python tests/gui_render.py -- "$(cygpath -w "$PWD")" host "$PORT" "$W" "$ENG" > "$OUT/host.log" 2>&1 &)
sleep 1
"$B" --factory-startup --python tests/gui_render.py -- "$(cygpath -w "$PWD")" guest "$PORT" "$W" "$ENG" > "$OUT/guest.log" 2>&1
sleep 4
echo "$OUT"
for r in host guest; do echo "== $r"; python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])), indent=1))" "$OUT/gui_render_$r.json"; done
grep -iE "Traceback|Error" "$OUT"/*.log | head -20
