"""Print one version's section of RELEASE_NOTES.md, for the GitHub release workflow.

Usage:  python tools/release_notes.py 1.0.2 [--checksum dist/ZuqongsoftMusic-Setup-1.0.2.exe.sha256]

With --checksum, the "<paste the checksum here when publishing>" placeholder is replaced
by the real SHA-256. Exits with an error if the version has no section.
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

notes = Path(__file__).resolve().parent.parent / "RELEASE_NOTES.md"
text = notes.read_text(encoding="utf-8")
sections = re.split(r"(?m)^(?=# .+? \d+\.\d+)", text)              # any app name, e.g. "# Garamut Music 1.0.4"
heading = re.compile(rf"# .+? {re.escape(args.version)}[ \t]*\r?\n")
section = next((s for s in sections if heading.match(s)), None)
if section is None:
    print(f"::error file=RELEASE_NOTES.md::No section '# <app name> {args.version}' in RELEASE_NOTES.md",
          file=sys.stderr)
    sys.exit(1)

body = section.split("\n", 1)[1].strip()          # the release title already shows the heading
body = re.sub(r"\n-{3,}\s*$", "", body).strip()      # trailing separator before the next version
if args.checksum:
    digest = Path(args.checksum).read_text(encoding="utf-8").split()[0]
    body = body.replace(PLACEHOLDER, digest)
print(body)