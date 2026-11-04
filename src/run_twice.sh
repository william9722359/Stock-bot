#!/usr/bin/env bash
set -eu
exec "${PYTHON:-python3}" "$(dirname "$0")/reproduce.py" "$@"
