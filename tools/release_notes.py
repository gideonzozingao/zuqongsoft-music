"""Print one version's section of RELEASE_NOTES.md, for the GitHub release workflow.

Usage:  python tools/release_notes.py 1.0.4 [--checksum dist/GaramutMusic-Setup-1.0.4.exe.sha256]

A section starts with a top-level heading naming the app and the version, e.g. "# Garamut Music 1.0.4"
(a "v" before the number is fine). With --checksum, the "<paste the checksum here when publishing>"
placeholder is replaced by the real SHA-256. If the version has no section, it exits with an error
that lists the headings it did find.
"""
import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER = "<paste the checksum here when publishing>"

# Always write UTF-8: Windows consoles default to a legacy code page (e.g. cp1252) that can't
# print characters like the arrows used in the notes.
sys.stdout.reconfigure(encoding="utf-8")

ap = argparse.ArgumentParser()
ap.add_argument("version")
ap.add_argument("--checksum", help=".sha256 file written by build_installer.bat")
args = ap.parse_args()
version = args.version.lstrip("vV")

notes = Path(__file__).resolve().parent.parent / "RELEASE_NOTES.md"
# utf-8-sig drops the invisible byte-order mark that Notepad and some PowerShell commands add.
text = notes.read_text(encoding="utf-8-sig").replace("\r\n", "\n")

HEADING = re.compile(r"(?m)^#[ \t]+(.+?)[ \t]+v?(\d+(?:\.\d+)+)[ \t]*$")     # "# <app name> 1.0.4"
starts = [(m.start(), m.group(2), m.group(0).strip()) for m in HEADING.finditer(text)]
section = None
for i, (pos, ver, _line) in enumerate(starts):
    if ver == version:
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        section = text[pos:end]
        break

if section is None:
    found = [line for _pos, _ver, line in starts][:5] or ["(no '# <app name> <version>' headings at all)"]
    print(f"::error file=RELEASE_NOTES.md::No section '# <app name> {version}' in RELEASE_NOTES.md. "
          f"Headings found: {' | '.join(found)}", file=sys.stderr)
    sys.exit(1)

body = section.split("\n", 1)[1].strip()            # the release title already shows the heading
body = re.sub(r"\n-{3,}\s*$", "", body).strip()      # trailing separator before the next version
if args.checksum:
    digest = Path(args.checksum).read_text(encoding="utf-8-sig").split()[0]
    body = body.replace(PLACEHOLDER, digest)
print(body)