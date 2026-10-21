"""Print the app version from version_info.txt (e.g. 1.2.0), for build_installer.bat."""
import re
from pathlib import Path

text = Path(__file__).resolve().parent.parent.joinpath("version_info.txt").read_text(encoding="utf-8")
m = re.search(r"StringStruct\('ProductVersion',\s*'([\d.]+)'\)", text)
parts = (m.group(1) if m else "1.0.0").split(".")
while len(parts) > 3 and parts[-1] == "0":      # 1.0.0.0 -> 1.0.0
    parts.pop()
print(".".join(parts))
