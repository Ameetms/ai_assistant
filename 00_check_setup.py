"""
STEP 0 - Check that your computer and project folder are ready.

What it does (it changes nothing, it only looks):
  1. Checks your Python version.
  2. Checks that the packages in requirements.txt are installed.
  3. Checks that the data folders from your GitHub repo are where we expect.

How to run (from inside your ai_assistant folder):
    python 00_check_setup.py
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (pip name, import name)
PACKAGES = [
    ("requests", "requests"),
    ("pandas", "pandas"),
    ("beautifulsoup4", "bs4"),
    ("lxml", "lxml"),
    ("openpyxl", "openpyxl"),
]

EXPECTED_FOLDERS = [
    "10-K_annual_reports",
    "10-Q_quarterly_reports",
    "tables",
]


def main() -> int:
    problems = 0
    print("=" * 60)
    print("SETUP CHECK")
    print("=" * 60)
    print(f"Project folder: {ROOT}\n")

    # 1. Python version
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    print(f"[{'OK' if ok else 'FAIL'}] Python {v.major}.{v.minor}.{v.micro}  (need 3.10 or newer; 3.11+ recommended)")
    problems += 0 if ok else 1

    # 2. Packages
    print()
    for pip_name, import_name in PACKAGES:
        try:
            mod = importlib.import_module(import_name)
            ver = getattr(mod, "__version__", "")
            print(f"[OK]   {pip_name} {ver}")
        except ImportError:
            print(f"[FAIL] {pip_name} is not installed")
            problems += 1

    # 3. Folders
    print()
    for name in EXPECTED_FOLDERS:
        p = ROOT / name
        if p.is_dir():
            n = sum(1 for f in p.rglob("*") if f.is_file())
            print(f"[OK]   {name}/  ({n} files)")
        else:
            print(f"[FAIL] folder '{name}' not found next to this script")
            problems += 1

    print()
    if problems:
        print(f"{problems} problem(s) found.")
        print("If packages are missing, run:   pip install -r requirements.txt")
        print("If folders are missing, move this script into your ai_assistant folder.")
        return 1
    print("Everything looks good. Next: python 01_inspect_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
