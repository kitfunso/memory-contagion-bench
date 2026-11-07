"""One command: install hippo-memory 3.3.3 if missing, run the grid, draw the figures. Run with Python 3.12+."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if not (ROOT / "node_modules" / "hippo-memory").exists():
        subprocess.run("npm ci", cwd=ROOT, shell=True, check=True)
    args = sys.argv[1:]
    subprocess.run([sys.executable, "-m", "contagion.grid", *args], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "contagion.figures"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
