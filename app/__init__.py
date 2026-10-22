"""Zuqongsoft Music package."""
import sys

# True when running as a built app (PyInstaller sets sys.frozen; Nuitka compiles every module
# with a __compiled__ global), False when running from source with python main.py.
BUNDLED = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
