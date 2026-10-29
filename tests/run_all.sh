#!/bin/bash
# Jalankan seluruh test suite hoodsniper (tanpa pytest: tiap file punya __main__ runner).
cd /root/workspace/cipher/projects/hoodsniper_v16_gabungan || exit 1
fail=0
for f in tests/test_*.py; do
  name=$(basename "$f")
  out=$(python3 "$f" 2>&1)
  rc=$?
  echo "=== $name rc=$rc"
  echo "$out" | tail -3
  if [ $rc -ne 0 ]; then fail=1; fi
done
echo "SUITE_FAIL=$fail"
