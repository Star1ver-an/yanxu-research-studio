"""Controlled entrypoint for an optional, isolated Python environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.analysis_worker import run

if __name__ == '__main__':
    sys.exit(run(*sys.argv[1:]))
