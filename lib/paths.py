"""No absolute machine path in a file that gets committed.

A template path under someone's home directory, written into `export_presets.cfg`,
works perfectly on the machine that wrote it and breaks on every other one -- and
it publishes a user name besides. It is the single
easiest thing to commit by accident, because the editor's own dialogs write it
for you: a template picker, a gamedata folder picker, an export path.

This module is the authority the pre-commit hook delegates to. It reads the
STAGED content by default -- what is actually about to be published -- rather
than the working tree, so a half-staged fix is judged by the half that ships.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

from .rules import BINARY_SUFFIXES, MACHINE_PATHS


class Finding:
    def __init__(self, path: str, line: int, text: str, kind: str):
        self.path, self.line, self.text, self.kind = path, line, text, kind

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}  {self.text}"


class GitUnavailable(RuntimeError):
    """git could not answer, and that is not the same as "nothing is staged".

    Both look identical on stdout -- empty -- and treating them alike turned this
    check into an unconditional pass everywhere git fails: outside a repository,
    on a clone owned by another account (`detected dubious ownership`), on a
    bind-mounted or container checkout. The command then printed a clean line and
    exited 0 over a tree full of machine paths.
    """


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        raise GitUnavailable(detail[-1] if detail else f"git {args[0]} exited {done.returncode}")
    return done.stdout


def _entries(out: str) -> list[str]:
    """Split `-z` output.

    `-z` is not a detail: in its default line mode git C-quotes any path with a
    non-ASCII byte in it -- `"caf\\303\\251.gd"`, quotes and octal escapes
    included -- and that string matches no file on disk and no blob in the index.
    Every such file was silently dropped, so a machine path inside one passed the
    check unseen. Game projects have those filenames by the dozen.
    """
    return [entry for entry in out.split("\0") if entry]


def staged_files(root: Path) -> list[str]:
    return _entries(_git(root, "diff", "--cached", "-z", "--name-only", "--diff-filter=ACM"))


def tracked_files(root: Path) -> list[str]:
    return _entries(_git(root, "ls-files", "-z"))


def _staged_content(root: Path, path: str) -> str | None:
    result = subprocess.run(["git", "show", f":{path}"], cwd=root,
                            capture_output=True, encoding="utf-8", errors="replace")
    return result.stdout if result.returncode == 0 else None


def _skipped(path: str, skip: list[str]) -> bool:
    if Path(path).suffix.lower() in BINARY_SUFFIXES:
        return True
    return any(fnmatch.fnmatch(path, pattern) for pattern in skip)


def scan_text(path: str, text: str, allow: list[str]) -> list[Finding]:
    permitted = [re.compile(a) for a in allow]
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern, kind in MACHINE_PATHS:
            for match in pattern.finditer(line):
                hit = match.group(0)
                if any(p.search(hit) for p in permitted):
                    continue
                found.append(Finding(path, number, hit, kind))
    return found


def scan(root: Path, skip: list[str], allow: list[str],
         staged: bool = True) -> tuple[list[Finding], list[str]]:
    """(findings, files that could not be read).

    The second half is returned rather than swallowed: a file this cannot read is
    a file it cannot clear, and reporting nothing for it is indistinguishable
    from reporting it clean.
    """
    files = staged_files(root) if staged else tracked_files(root)
    findings: list[Finding] = []
    unreadable: list[str] = []
    for path in files:
        if _skipped(path, skip):
            continue
        text = _staged_content(root, path) if staged else None
        if text is None:
            file = root / path
            if not file.is_file():
                # Deleted, a submodule, or a name this could not resolve. Only
                # the staged view is expected to be complete on disk.
                if staged:
                    unreadable.append(path)
                continue
            try:
                # Decode the way the staged branch does. Reading with strict
                # UTF-8 and swallowing the error meant the same cp1252 file was
                # flagged at commit time and reported clean by `review` forever
                # after -- the two halves of one function disagreeing.
                text = file.read_bytes().decode("utf-8", "replace")
            except OSError:
                unreadable.append(path)
                continue
        findings.extend(scan_text(path, text, allow))
    return findings, unreadable
