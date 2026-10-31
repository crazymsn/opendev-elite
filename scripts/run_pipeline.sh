#!/bin/bash
set -e

echo "Starting full pipeline..."

cd "$(dirname "$0")/.."
python3 main.py init
python3 main.py schedule --config config/discovery.json
python3 main.py work --max-jobs 20
python3 main.py report

echo "Pipeline completed."
