#!/bin/bash
set -e

PYTHON=$(which python3 || echo "")
if [ -z "$PYTHON" ]; then
  echo "python3 not found, abort."
  exit 1
fi

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'

echo "Environment setup complete."
