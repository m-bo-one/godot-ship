"""What is actually inside the pack.

The export dialog reports how many megabytes it wrote and nothing about the
content, so everything a build must not carry is invisible in that number: a dev
addon is under a megabyte, a config file with your toolchain in it is under a
kilobyte, and a missing translation table is zero. The pack directory is parsed
directly instead.

An encrypted pack encrypts its directory too, so this cannot read a release
build -- that is the price of `encrypt_directory`, and the reason a project
audits an unencrypted export of the same tree.
"""

from __future__ import annotations

import fnmatch
import struct
from collections import defaultdict
from pathlib import Path

MAGIC = b"GDPC"


def find_pack(path: Path) -> tuple[bytes, int]:
    """The pack bytes and where GDPC starts inside them."""
    data = path.read_bytes()
    if data[:4] == MAGIC:
        return data, 0
    # Embedded in an executable: the file ends with <pck_size u64><GDPC>.
    if data[-4:] == MAGIC:
        (size,) = struct.unpack_from("<Q", data, len(data) - 12)
        start = len(data) - 12 - size
        if data[start:start + 4] == MAGIC:
            return data, start
    for sibling in (path.with_suffix(".pck"), path.parent / "index.pck"):
        if sibling != path and sibling.is_file():
            return find_pack(sibling)
    raise SystemExit(f"no GDPC magic in {path} and no .pck beside it")


def read_pack(data: bytes, base: int):
    """Pack format v1-v4. v4 (Godot 4.7+) moved the directory to its own offset
    and dropped the `res://` prefix from the stored paths."""
    off = base + 4
    version, major, minor, patch = struct.unpack_from("<IIII", data, off)
    off += 16
    if version >= 2:
        off += 4   # pack_flags
        off += 8   # file_base
    if version >= 4:
        (directory,) = struct.unpack_from("<Q", data, off)
        off = base + directory
    else:
        off += 16 * 4  # reserved

    (count,) = struct.unpack_from("<I", data, off)
    off += 4
    entries = []
    for _ in range(count):
        (length,) = struct.unpack_from("<I", data, off)
        off += 4
        path = data[off:off + length].rstrip(b"\0").decode("utf-8", "replace")
        off += length
        _offset, size = struct.unpack_from("<QQ", data, off)
        off += 16 + 16  # offset, size, md5
        if version >= 2:
            off += 4  # flags
        entries.append((path.removeprefix("res://"), size))
    return (version, major, minor, patch), entries


def summarise(entries) -> None:
    by_ext = defaultdict(lambda: [0, 0])
    by_dir = defaultdict(lambda: [0, 0])
    for path, size in entries:
        ext = Path(path).suffix.lstrip(".") or "(none)"
        by_ext[ext][0] += 1
        by_ext[ext][1] += size
        head = str(Path(path).parent).replace("\\", "/")
        by_dir[head if head != "." else "(root)"][0] += 1
        by_dir[head if head != "." else "(root)"][1] += size

    print("=== by extension ===")
    for ext, (n, size) in sorted(by_ext.items(), key=lambda e: -e[1][1])[:15]:
        print(f"{ext:12} {n:6}  {size / 1048576:8.2f} MB")
    print("\n=== by directory ===")
    for head, (n, size) in sorted(by_dir.items(), key=lambda e: -e[1][1])[:15]:
        print(f"{head:48} {n:6}  {size / 1048576:8.2f} MB")


def check(entries, forbidden: list[str], required: list) -> int:
    """Exit code: 0 clean, 1 something shipped that must not, or is missing."""
    paths = [p for p, _ in entries]
    bad = 0
    for pattern in forbidden:
        hits = [p for p in paths if fnmatch.fnmatch(p, pattern)]
        if hits:
            bad += 1
            print(f"SHOULD NOT SHIP  {pattern} -> {len(hits)} file(s)")
            for path in hits[:8]:
                print(f"                   {path}")
            if len(hits) > 8:
                print(f"                   ... and {len(hits) - 8} more")
    for patterns, expected in required:
        found = len([p for p in paths if any(fnmatch.fnmatch(p, q) for q in patterns)])
        if found != expected:
            bad += 1
            print(f"MISSING          {' | '.join(patterns)}: {found} present, {expected} expected")
    if bad:
        print(f"\n{bad} problem(s). Fix the preset's exclude_filter, or the build.")
        return 1
    print("pack contents OK: nothing forbidden, everything required is present")
    return 0
