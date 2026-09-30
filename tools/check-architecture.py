#!/usr/bin/env python3
"""Structural checks for architecture.html.

Run:  python3 tools/check-architecture.py [path]

Validates the four failure modes this document has actually had:
  1. unclosed / mismatched tags (large in-place edits have broken nesting before)
  2. duplicate SVG ids, and marker references with no matching <marker id>
  3. SVG <text> that overflows its enclosing panel (a canvas-bounds check misses this)
  4. external assets, which would break a bare file:// open

Exit 0 = clean. Non-zero = do not consider the edit finished.
"""
from __future__ import annotations

import html as htmllib
import re
import sys
from html.parser import HTMLParser

DOC = sys.argv[1] if len(sys.argv) > 1 else "architecture.html"

VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base",
        "col", "embed", "source", "track", "wbr"}

# class -> (font-size px, avg advance as a fraction of font-size)
FONTS = {
    "s-title": (14, 0.60),
    "s-sub": (11.5, 0.62),
    "s-lbl": (12, 0.58),
    "s-mono": (11, 0.62),
    "s-tiny": (10, 0.62),
}
WIDE = set("—→←▶°·§×≤≥…")  # roughly one full em in these faces


class Balance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}> at {self.getpos()}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                unclosed = [t for t, _ in self.stack[i + 1:]]
                self.errors.append(
                    f"</{tag}> at {self.getpos()} closes over unclosed {unclosed}")
                del self.stack[i:]
                return
        self.errors.append(f"stray </{tag}> at {self.getpos()}")


def width(text: str, size: float, advance: float) -> float:
    """Entity-decoded string width. Entities must be decoded first."""
    return sum(size * (1.0 if ch in WIDE else advance) for ch in htmllib.unescape(text))


def main() -> int:
    source = open(DOC, encoding="utf-8").read()
    problems: list[str] = []

    # 1. tag balance
    bal = Balance()
    bal.feed(source)
    problems += bal.errors
    problems += [f"unclosed <{t}> at {pos}" for t, pos in bal.stack]

    # 2. ids and marker references
    import collections
    ids = re.findall(r'\sid="([^"]+)"', source)
    for ident, n in collections.Counter(ids).items():
        if n > 1:
            problems.append(f"duplicate id {ident} x{n}")
    refs = set(re.findall(r"url\(#([^)]+)\)", source))
    for ref in sorted(refs - set(ids)):
        problems.append(f"url(#{ref}) has no matching id")
    for ident in sorted(set(ids) - refs):
        if ident.startswith(("a", "b", "g", "k", "l", "n", "p", "q", "r", "s",
                             "t", "u", "z")) and ident[1:].isdigit():
            problems.append(f"marker id {ident} defined but never referenced")

    # 3. SVG text against its enclosing panel
    for n, m in enumerate(re.finditer(r"<svg\b.*?</svg>", source, re.S), 1):
        body = m.group(0)
        line = source[:m.start()].count("\n") + 1
        box = re.search(r'viewBox="([\d.\s]+)"', body).group(1).split()
        canvas_w = float(box[2])
        rects = [tuple(map(float, r)) for r in re.findall(
            r'<rect[^>]*?x="([-\d.]+)"[^>]*?y="([-\d.]+)"'
            r'[^>]*?width="([\d.]+)"[^>]*?height="([\d.]+)"', body)]
        for tm in re.finditer(r"<text([^>]*)>(.*?)</text>", body, re.S):
            attrs, text = tm.group(1), re.sub(r"<[^>]+>", "", tm.group(2))
            if not text.strip():
                continue
            size, advance = 10, 0.62
            cls = re.search(r'class="([\w-]+)"', attrs)
            if cls and cls.group(1) in FONTS:
                size, advance = FONTS[cls.group(1)]
            fs = re.search(r'font-size="([\d.]+)"', attrs)
            if fs:
                size, advance = float(fs.group(1)), 0.60
            x = float(re.search(r'\bx="([-\d.]+)"', attrs).group(1))
            y = float(re.search(r'\by="([-\d.]+)"', attrs).group(1))
            a = re.search(r'text-anchor="(\w+)"', attrs)
            anchor = a.group(1) if a else "start"
            w = width(text, size, advance)
            x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
            x1 = x0 + w
            if x1 > canvas_w - 4:
                problems.append(f"svg#{n}:{line} canvas overflow y={y} {x1:.0f}>{canvas_w} {text[:50]!r}")
            holders = [r for r in rects
                       if r[0] - 2 <= x0 <= r[0] + r[2] - 3 and r[1] <= y <= r[1] + r[3]]
            if holders:
                r = min(holders, key=lambda r: r[2] * r[3])
                if x1 > r[0] + r[2] - 5:
                    problems.append(
                        f"svg#{n}:{line} panel overflow y={y} {x1:.0f}>{r[0] + r[2]:.0f} {text[:55]!r}")

    # 4. self-contained
    for asset in re.findall(r'(?:src|href)="(?!#)([^"]+)"', source):
        problems.append(f"external asset: {asset}")

    for p in problems:
        print(f"FAIL  {p}")
    print(f"{DOC}: {len(problems)} problem(s), "
          f"{source.count(chr(10)) + 1} lines, {len(source.encode())} bytes")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
