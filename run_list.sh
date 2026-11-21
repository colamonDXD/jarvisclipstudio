# usage: run_list.sh <tree-dir> <list-file> <out-file>
SP="/c/Users/PRATEE~1/AppData/Local/Temp/claude/3c41ff4fb4db0771/a5a5ba73-1a6f-4879-b6c9-c129a05c5ba3/scratchpad"
PY=/d/SnipNova-Dev/claude-code/backend/.venv/Scripts/python.exe
: > "$3"
mapfile -t FILES < "$2"
for f in "${FILES[@]}"; do
  out=$(cd "$1/backend" && NO_PROXY=localhost PYTHONPATH="$SP/shim" timeout 180 "$PY" -m pytest "$f" -q -p no:cacheprovider --no-cov < /dev/null 2>&1 | grep -E "^FAILED|passed|failed|skipped|error" | tr '\n' ' ')
  echo "$f :: ${out:-TIMEOUT(180s)/NO-OUTPUT}" >> "$3"
done
echo DONE >> "$3"
