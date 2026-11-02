"""Print the app version from version_info.txt (e.g. 1.0.4), for build_installer.bat and the
release workflow.

The version lives ONLY in version_info.txt: change all four numbers there (filevers, prodvers,
FileVersion, ProductVersion) for each release. If the version can't be read, this stops with an
error instead of guessing, so a build is never labelled with the wrong version.
"""
import re
import sys
from pathlib import Path

text = Path(__file__).resolve().parent.parent.joinpath("version_info.txt").read_text(encoding="utf-8")
m = re.search(r"StringStruct\('ProductVersion',\s*'([\d.]+)'\)", text)
if not m:
    print("ERROR: no ProductVersion found in version_info.txt", file=sys.stderr)
    sys.exit(1)
parts = m.group(1).split(".")
while len(parts) > 3 and parts[-1] == "0":      # 1.0.4.0 -> 1.0.4
    parts.pop()
print(".".join(parts))