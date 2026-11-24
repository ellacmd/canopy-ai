"""Validate Conventional Commit subjects for local commits and PR squash titles."""

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--title":
        subject = sys.argv[2]
    elif len(sys.argv) == 2:
        lines = Path(sys.argv[1]).read_text().splitlines()
        subject = lines[0] if lines else ""
    else:
        print("Pass a commit message file, or --title followed by a PR title.")
        return 2
    pattern = (
        r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
        r"(\([a-zA-Z0-9_./-]+\))?!?: .{1,100}$"
    )
    if not re.fullmatch(pattern, subject):
        print("Use a Conventional Commit, e.g. feat(refunds): support partial returns")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
