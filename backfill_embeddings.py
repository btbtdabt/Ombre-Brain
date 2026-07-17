"""Compatibility launcher for the maintained embedding backfill CLI."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent / "tools" / "backfill_embeddings.py"),
        run_name="__main__",
    )
