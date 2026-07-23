#!/usr/bin/env python3
"""Fail if any given file (staged content or commit message) contains an AI
co-author signature.

Fayna policy: published / portfolio repos must not credit an AI assistant.
Wired in `.pre-commit-config.yaml` as two local hooks — one over staged file
contents (pass_filenames), one over the commit message (stage: commit-msg).
"""

import re
import sys

PATTERN = re.compile(
    r'co-authored-by:\s*.*(claude|anthropic)' r'|generated with[^\n]*claude' r'|noreply@anthropic',
    re.IGNORECASE,
)


def main(paths: list[str]) -> int:
    offenders = []
    for path in paths:
        try:
            with open(path, encoding='utf-8', errors='ignore') as fh:
                if PATTERN.search(fh.read()):
                    offenders.append(path)
        except (IsADirectoryError, FileNotFoundError):
            continue
    if offenders:
        print('❌ AI co-author signature found in:')
        for path in offenders:
            print(f'   - {path}')
        print('Remove it before committing (Fayna no-AI-signature policy).')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
