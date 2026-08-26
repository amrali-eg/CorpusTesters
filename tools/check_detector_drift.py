"""Fail if the detector sources have drifted between the three repositories.

The encoding detector is copy-pasted across EncodingChecker,
LineEndingNormalizer and this repository's CorpusTesting. Nothing in the build
enforces that the copies agree, so a defect fixed in one silently survives in
the other two - which is exactly what happened with the strict-fallback defect
(EncodingChecker#36, LineEndingNormalizer#9), found only because an audit
happened to look.

This turns that into a build failure.

Two kinds of check:

  Whole file   TextValidation.cs and UnicodeDetector.cs must be identical
               across all three, modulo the namespace declaration and a
               `using System;` that is present only where the project does not
               enable ImplicitUsings.

  Named member TextEncoding.cs legitimately differs - each repository adds its
               own helpers - so only the members that must stay in lockstep are
               compared, by name.

Usage:
    python tools/check_detector_drift.py <ec-root> <len-root> [ct-root]

Exit codes: 0 identical, 1 drift found, 2 a file or member is missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_MISSING = 2

# Relative path of the detector sources inside each repository.
ROOTS = {
    "EncodingChecker": Path("sources/EncodingChecker"),
    "LineEndingNormalizer": Path("."),
    "CorpusTesting": Path("CorpusTesting"),
}

# Must be identical everywhere.
WHOLE_FILES = ["TextValidation.cs", "UnicodeDetector.cs"]

# Files that legitimately differ, but whose listed members must not.
SHARED_MEMBERS = {
    "TextEncoding.cs": ["Strict"],
}


def normalize(source: str) -> str:
    """Strip the differences that are project configuration, not logic.

    The namespace is necessarily different. `using System;` is present only in
    projects without ImplicitUsings, which is a csproj setting rather than a
    behavioural difference. Everything else has to match exactly - including
    every other using, so a genuinely divergent import is still caught.
    """
    source = source.lstrip("﻿").replace("\r\n", "\n")

    kept = [
        line for line in source.split("\n")
        if not line.startswith("namespace ")
        and line.strip() != "using System;"
    ]

    # Trailing whitespace and blank runs are formatting, not drift.
    text = "\n".join(line.rstrip() for line in kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_member(source: str, name: str) -> str | None:
    """Return a named C# method with its body, brace-matched.

    Deliberately simple: the members this guards are ordinary methods, and a
    parser that silently mis-slices would be worse than one that fails loudly.
    """
    source = source.lstrip("﻿").replace("\r\n", "\n")

    match = re.search(
        r"^[ \t]*(?:internal|public|private|protected)[^\n(]*\b"
        + re.escape(name) + r"\s*\(",
        source,
        re.MULTILINE,
    )
    if match is None:
        return None

    opening = source.find("{", match.end())
    if opening == -1:
        return None

    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return normalize(source[match.start():index + 1])

    return None


def report(label: str, texts: dict[str, str]) -> bool:
    """Print a verdict for one comparison. Returns True when they agree."""
    distinct = {}
    for repo, text in texts.items():
        distinct.setdefault(text, []).append(repo)

    if len(distinct) == 1:
        print(f"  OK    {label}")
        return True

    print(f"  DRIFT {label}")
    groups = sorted(distinct.values(), key=len, reverse=True)
    for group in groups:
        print(f"          group: {', '.join(group)}")

    import difflib

    majority, minority = groups[0], groups[1]
    left = next(t for t, r in distinct.items() if r == majority)
    right = next(t for t, r in distinct.items() if r == minority)

    diff = difflib.unified_diff(
        left.split("\n"), right.split("\n"),
        fromfile=f"{majority[0]}/{label}", tofile=f"{minority[0]}/{label}",
        lineterm="", n=2,
    )
    for line in list(diff)[:60]:
        print(f"          {line}")
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return EXIT_MISSING

    repos = {
        "EncodingChecker": Path(argv[1]) / ROOTS["EncodingChecker"],
        "LineEndingNormalizer": Path(argv[2]) / ROOTS["LineEndingNormalizer"],
        "CorpusTesting": Path(argv[3] if len(argv) > 3 else ".") / ROOTS["CorpusTesting"],
    }

    for repo, path in repos.items():
        if not path.is_dir():
            print(f"missing source directory for {repo}: {path}", file=sys.stderr)
            return EXIT_MISSING

    print("Detector sources, across three repositories:")
    for repo, path in repos.items():
        print(f"  {repo:<22} {path}")
    print()

    ok = True

    print("Whole-file comparison (modulo namespace and implicit usings):")
    for filename in WHOLE_FILES:
        texts = {}
        for repo, path in repos.items():
            source = path / filename
            if not source.is_file():
                print(f"  MISSING {repo}/{filename}", file=sys.stderr)
                return EXIT_MISSING
            texts[repo] = normalize(source.read_text(encoding="utf-8-sig"))
        ok &= report(filename, texts)

    print()
    print("Shared members in files that otherwise differ:")
    for filename, members in SHARED_MEMBERS.items():
        for member in members:
            texts = {}
            for repo, path in repos.items():
                source = path / filename
                if not source.is_file():
                    print(f"  MISSING {repo}/{filename}", file=sys.stderr)
                    return EXIT_MISSING
                body = extract_member(
                    source.read_text(encoding="utf-8-sig"), member)
                if body is None:
                    print(f"  MISSING {repo}/{filename}::{member}", file=sys.stderr)
                    return EXIT_MISSING
                texts[repo] = body
            ok &= report(f"{filename}::{member}", texts)

    print()
    if ok:
        print("No drift: every shared source agrees across all three repositories.")
        return EXIT_OK

    print(
        "Drift detected. These sources are copies of one another; a change to\n"
        "one must be applied to all three. See the README section on shared code."
    )
    return EXIT_DRIFT


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
