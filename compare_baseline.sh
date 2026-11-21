cd /d/SnipNova-Dev/claude-code
until grep -q DONE test-footage/affected_results.txt; do sleep 10; done
SP="/c/Users/PRATEE~1/AppData/Local/Temp/claude/3c41ff4fb4db0771/a5a5ba73-1a6f-4879-b6c9-c129a05c5ba3/scratchpad"
: > test-footage/baseline_compare.txt
grep -v DONE test-footage/affected_results.txt | grep -viE ":: .*(passed|skipped)( |$)" | grep -viE " passed" | while IFS= read -r line; do
  f="${line%% :: *}"
  out=$(cd /d/SnipNova-Dev/baseline-wt/backend && NO_PROXY=localhost PYTHONPATH="$SP/shim" timeout 150 /d/SnipNova-Dev/claude-code/backend/.venv/Scripts/python.exe -m pytest "$f" -q -p no:cacheprovider --no-cov 2>&1 | tail -1)
  echo "$f :: BRANCH=[${line#* :: }] BASELINE=[${out:-TIMEOUT/NO-OUTPUT}]" >> test-footage/baseline_compare.txt
done
echo DONE >> test-footage/baseline_compare.txt
