#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
PYTHONPATH=. python -m unittest discover -s tests -v
python -m compileall -q hansker_updater tests
