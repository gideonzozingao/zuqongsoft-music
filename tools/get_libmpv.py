"""Download libmpv-2.dll (Windows x64) into ./lib for the mpv audio engine.

Run:  python tools/get_libmpv.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import libmpv  # noqa: E402


def main() -> int:
    target = ROOT / "lib" / libmpv.DLL_NAMES[0]
    if target.exists():
        print(f"{target} already present.")
        return 0

    def progress(done: int, total: int) -> None:
        if total:
            print(f"\r  {done / 1e6:5.1f} / {total / 1e6:.1f} MB", end="" if done < total else "\n", flush=True)

    try:
        libmpv.download(ROOT / "lib", progress)
        return 0
    except RuntimeError as exc:
        print(f"\nERROR: {exc}")
        print(f"Manual fix: download mpv-dev-x86_64-*.7z from https://github.com/{libmpv.REPO}/releases,")
        print(f"extract libmpv-2.dll and put it in {ROOT / 'lib'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())