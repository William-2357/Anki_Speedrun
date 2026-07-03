# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Runtime-only PDF text extraction for the leakage wall (stdlib only).

Extracts the text-showing operators (Tj / TJ / ' / ") from FlateDecode
content streams so generated stems can be n-gram-checked against the local
CFA reference PDF. The extracted text is used IN MEMORY for the check and is
never written to disk or committed anywhere (provenance/leakage wall - the
reference is a local, git-ignored, authoring-time file).

This is a best-effort extractor: good enough to catch verbatim copying, not a
general PDF-to-text tool. Kerning arrays inside TJ are concatenated without
injected spaces (word gaps live inside the string chunks); distinct text
operators are separated by spaces.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
# (string) Tj   |   [ ... ] TJ   |   (string) '   |   (a b string) "
_TJ_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|')|\[(?:[^\[\]\\]|\\.)*?\]\s*TJ")
_CHUNK_RE = re.compile(rb"\((?:\\.|[^\\()])*\)")

_OCTAL_RE = re.compile(rb"\\([0-7]{1,3})")


def _decode_pdf_string(raw: bytes) -> str:
    """Decode the inside of a (...) PDF literal string."""
    out = bytearray()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == 0x5C and i + 1 < len(raw):  # backslash
            nxt = raw[i + 1]
            if nxt in b"nrtbf":
                out.extend(
                    {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f"}[
                        nxt
                    ]
                )
                i += 2
                continue
            if nxt in b"()\\":
                out.append(nxt)
                i += 2
                continue
            m = _OCTAL_RE.match(raw, i)
            if m:
                out.append(int(m.group(1), 8) & 0xFF)
                i = m.end()
                continue
            i += 2
            continue
        out.append(b)
        i += 1
    return out.decode("latin-1", errors="replace")


def extract_pdf_text(path: str | Path) -> str:
    """Best-effort plain text of a PDF's page content streams."""
    data = Path(path).read_bytes()
    pieces: list[str] = []
    for m in _STREAM_RE.finditer(data):
        try:
            content = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        if b"Tj" not in content and b"TJ" not in content:
            continue
        for op in _TJ_RE.finditer(content):
            token = op.group(0)
            # Concatenate the (...) chunks of this operator without added
            # spaces: kerning numbers between chunks are sub-word spacing,
            # and real word gaps are carried inside the chunks themselves.
            text = "".join(
                _decode_pdf_string(c[1:-1]) for c in _CHUNK_RE.findall(token)
            )
            if text:
                pieces.append(text)
    return " ".join(pieces)


def tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric tokens (shared by the n-gram wall)."""
    return re.findall(r"[a-z0-9]+", text.lower())


def ngram_set(tokens: list[str], n: int = 8) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}
