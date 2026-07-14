#!/usr/bin/env bash
set -euo pipefail

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

python main.py --test
python main.py --peak-load
python main.py --low-load
