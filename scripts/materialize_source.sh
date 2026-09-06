#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUNDLE="artifacts/Enterprise_Data_Retrieval_Control_Hub_Implementation_Starter.zip"
if [[ ! -f "$BUNDLE" ]]; then
  echo "Missing implementation bundle: $BUNDLE" >&2
  exit 1
fi

unzip -o "$BUNDLE" -d . >/dev/null
find enterprise_delivery -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find enterprise_delivery -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
find enterprise_delivery -type f \( -name '*.pyc' -o -name '*.db' \) -delete 2>/dev/null || true

echo "Materialized implementation source under enterprise_delivery/."
