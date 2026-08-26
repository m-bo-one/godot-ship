"""Enough YAML for a config file, with no dependency.

PyYAML is used when it is installed. It usually is not on a fresh machine, and
`pip install` before you can build is exactly the friction this skill exists to
remove -- so this reads the subset that `ship.py init` writes and that a person
edits by hand afterwards:

    key: value                  scalars: strings, ints, true/false
    key:                        nested maps by indentation
      inner: value
    list:                       block lists of scalars
      - one
      - two
    entries:                    block lists of maps
      - paths: ["a", "b"]       inline lists on one line
        count: 1
    # comments, and blank lines

Anchors, multi-line scalars, flow maps and the rest of YAML are not read. If a
project needs them, install PyYAML and this file steps aside.
"""

from __future__ import annotations

import ast
import re


def loads(text: str, *, prefer_pyyaml: bool = True):
    """`prefer_pyyaml=False` forces the hand parser below.

    That is what `ship.py init` checks its own output with: PyYAML on the machine
    that WRITES a config hides any construct this parser cannot read, and the
    next machine -- usually the one without PyYAML -- meets it as every command
    failing at once.
    """
    if prefer_pyyaml:
        try:
            import yaml  # PyYAML handles everything; prefer it whenever present
            return yaml.safe_load(text) or {}
        except ImportError:
            pass
    lines = _significant(text)
    if not lines:
        return {}
    value, index = _block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"line {lines[index][2]}: unexpected indentation")
    return value


def _significant(text: str) -> list[tuple[int, str, int]]:
    """(indent, content, line number), dropping blanks and whole-line comments."""
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((len(raw) - len(raw.lstrip(" ")), stripped, number))
    return out


def _block(lines, index: int, indent: int):
    if lines[index][1].startswith("- "):
        return _list(lines, index, indent)
    return _map(lines, index, indent)


def _map(lines, index: int, indent: int):
    out: dict = {}
    while index < len(lines):
        level, content, number = lines[index]
        if level < indent:
            break
        if level > indent:
            raise ValueError(f"line {number}: unexpected indentation")
        if content.startswith("- "):
            break
        match = re.match(r"^([^:#]+):\s*(.*)$", content)
        if not match:
            raise ValueError(f"line {number}: not a key")
        key, rest = match.group(1).strip(), match.group(2).strip()
        index += 1
        if rest and not rest.startswith("#"):
            out[key] = _scalar(rest)
        elif index < len(lines) and lines[index][0] > level:
            out[key], index = _block(lines, index, lines[index][0])
        else:
            out[key] = None
    return out, index


def _list(lines, index: int, indent: int):
    out: list = []
    while index < len(lines):
        level, content, _number = lines[index]
        if level != indent or not content.startswith("- "):
            break
        body = content[2:].strip()
        # `- key: value` starts a map whose remaining keys are indented to the
        # column the key itself sits in, two past the dash.
        if re.match(r"^[^:#]+:(\s|$)", body):
            inner_indent = level + 2
            synthetic = [(inner_indent, body, _number)]
            index += 1
            while index < len(lines) and lines[index][0] >= inner_indent and \
                    not lines[index][1].startswith("- "):
                synthetic.append(lines[index])
                index += 1
            value, _ = _map(synthetic, 0, inner_indent)
            out.append(value)
        else:
            out.append(_scalar(body))
            index += 1
    return out, index


def _scalar(text: str):
    text = _strip_comment(text)
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~", ""):
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part.strip()) for part in _split(inner)] if inner else []
    if (text[0] == text[-1] and text[0] in "\"'") and len(text) >= 2:
        return ast.literal_eval(text)
    try:
        return int(text)
    except ValueError:
        return text


def _strip_comment(text: str) -> str:
    """A `#` inside quotes is content; outside them it starts a comment."""
    quote = ""
    for position, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (position == 0 or text[position - 1] == " "):
            return text[:position].strip()
    return text.strip()


def _split(inner: str) -> list[str]:
    parts, depth, quote, current = [], 0, "", ""
    for char in inner:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current)
    return parts
