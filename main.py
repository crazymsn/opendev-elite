"""Cross-platform entry point; no model credentials or C++ build required."""
from worker.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
