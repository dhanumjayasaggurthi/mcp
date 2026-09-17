#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLATFORM_ROOT=$(dirname -- "$SCRIPT_DIR")
export PYTHONPATH="$PLATFORM_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python -m enterprise_data_platform.workers migrate
