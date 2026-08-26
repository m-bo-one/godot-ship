"""Install and configure GDMaim, and keep its lock list honest.

Obfuscation is not a switch you flip once. Three things have to be true at the
same time or the exported build breaks in ways the editor never shows:

* the preset must hand GDMaim plain text (`script_export_mode=0`) -- Godot's own
  tokenizer would give it a `.gdc` with nothing left to rewrite;
* every symbol the game reaches through a STRING -- `call("x")`,
  `has_method("x")`, `connect("x", …)` -- must be locked by name, because GDMaim
  renames the declaration and cannot rename a string literal;
* the addon's own source must stay out of the pack.

The settings written here are the ones three projects arrived at separately.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

UPSTREAM = "https://github.com/AzuraDreamer/gdmaim.git"
PLUGIN = "res://addons/gdmaim/plugin.cfg"

# GDMaim reads .gdmaim/export.cfg BEFORE addons/gdmaim/export.cfg and writes
# both, so a value set in only one of them is silently the old value.
CONFIG_FILES = [".gdmaim/export.cfg", "addons/gdmaim/export.cfg"]

SETTINGS = {
    "obfuscator": {
        "enabled": "true",
        # A binary .res holding exported properties cannot be rewritten the way
        # a .tscn can, and renaming them loses whatever it configured.
        "export_vars": "false",
        "shuffle_top_level": "false",
        "inline_consts": "false",
        "inline_enums": "false",
        "preprocessor_prefix": '"##"',
    },
    "post_process": {
        "strip_comments": "true",
        "strip_empty_lines": "true",
        # It turns a wrapped expression whose continuation starts with an
        # operator into `+0.15`, which GDScript reads as a signed literal, and
        # the script fails to parse at run time. It saves nothing besides.
        "strip_extraneous_spacing": "false",
        "strip_editor_annotations": "true",
        "strip_static_typing": "false",
        "striped_static_typing_be_initialized": "true",
        "regex_filter_enabled": "false",
        "feature_filters": "true",
        # 0 = text. gdbc is not vendored, so the addon does the tokenizing.
        "export_mode": "0",
    },
    "id": {
        "prefix": '"__"',
        "target_length": "4",
        "dynamic_seed": "false",
    },
    "source_mapping": {
        "filepath": '"res://.dev/gdmaim"',
        "max_files": "10",
        "compress": "true",
        # It prepends a print of the map's filename to the first autoload, so a
        # player's log would name the deobfuscation artifact and the build time.
        # On 0.3.9 the same branch also registers autoloads as global symbols --
        # check the linkage after an upgrade, `ship.py obfuscate --check`.
        "inject_name": "false",
    },
}

# Either `obj.call("x")` or a bare `call("x")` on self -- the bare form is the
# common one inside the class that declares the method, and requiring the dot
# missed exactly those. The alternation keeps `my_connect(` from matching: the
# lookbehind rejects a name that runs on from a word character.
REACHED = re.compile(
    r"(?:\.|(?<![A-Za-z0-9_]))"
    r"(?:call|callv|call_deferred|call_thread_safe|has_method|has_signal|connect"
    r"|disconnect|is_connected|emit_signal)\(\s*&?\"(\w+)\"")
CALLABLE = re.compile(r"Callable\([^,()]+,\s*&?\"(\w+)\"")
DECLARED = re.compile(r"(?m)^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?func\s+(\w+)\s*\(")
SIGNAL = re.compile(r"(?m)^\s*signal\s+(\w+)")


def installed(root: Path) -> bool:
    return (root / "addons/gdmaim/plugin.cfg").is_file()


def install(root: Path, source: str | None) -> None:
    """Copy the addon in from a local checkout, or clone it."""
    target = root / "addons/gdmaim"
    if installed(root):
        print("  addons/gdmaim already there")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source and (Path(source) / "addons/gdmaim").is_dir():
        shutil.copytree(Path(source) / "addons/gdmaim", target,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        print(f"  addons/gdmaim <- {source}")
        return
    if source and (Path(source) / "plugin.cfg").is_file():
        shutil.copytree(Path(source), target, ignore=shutil.ignore_patterns(".git"))
        print(f"  addons/gdmaim <- {source}")
        return
    print(f"  cloning {UPSTREAM}")
    temporary = root / ".gdmaim-clone"
    subprocess.run(["git", "clone", "--depth", "1", UPSTREAM, str(temporary)], check=True)
    shutil.copytree(temporary / "addons/gdmaim", target)
    shutil.rmtree(temporary, ignore_errors=True)
    print("  addons/gdmaim <- upstream")


def enable_plugin(root: Path) -> None:
    """`[editor_plugins] enabled` is what makes the export hook run at all."""
    manifest = root / "project.godot"
    text = manifest.read_text(encoding="utf-8")
    if PLUGIN in text:
        return
    section = re.search(r'(?m)^enabled=PackedStringArray\((.*)\)$', text)
    if section:
        inner = section.group(1).strip()
        joined = f'"{PLUGIN}"' if not inner else f'{inner}, "{PLUGIN}"'
        text = text[:section.start()] + f"enabled=PackedStringArray({joined})" + text[section.end():]
    else:
        text = text.rstrip("\n") + \
            f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")\n'
    manifest.write_text(text, encoding="utf-8")
    print("  plugin enabled in project.godot")


def write_settings(root: Path) -> None:
    for name in CONFIG_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(_merged(existing), encoding="utf-8")
    print(f"  settings written to {' and '.join(CONFIG_FILES)}")


def _merged(existing: str) -> str:
    """Keep whatever the addon put there, override only what is settled."""
    out: list[str] = []
    seen: dict[str, set[str]] = {}
    section = ""
    for line in existing.splitlines():
        header = re.match(r"^\[(\w+)\]$", line.strip())
        if header:
            section = header.group(1)
            seen.setdefault(section, set())
            out.append(line)
            continue
        key = re.match(r"^(\w+)=", line.strip())
        if key and section in SETTINGS and key.group(1) in SETTINGS[section]:
            name = key.group(1)
            seen[section].add(name)
            out.append(f"{name}={SETTINGS[section][name]}")
            continue
        out.append(line)
    for section, values in SETTINGS.items():
        missing = {k: v for k, v in values.items() if k not in seen.get(section, set())}
        if not missing:
            continue
        if section not in seen:
            out += ["", f"[{section}]", ""]
        for key, value in missing.items():
            out.append(f"{key}={value}")
    return "\n".join(out).strip() + "\n"


# ------------------------------------------------------------------ lock list

def _project_scripts(root: Path, folders: list[str]) -> list[Path]:
    found: list[Path] = []
    for folder in folders:
        base = root / folder
        if base.is_dir():
            found += [p for p in base.rglob("*.gd") if "addons/gdmaim" not in p.as_posix()]
        elif base.suffix == ".gd" and base.is_file():
            found.append(base)
    return found


def locks(root: Path, folders: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(needed, missing, stale) -- symbols reached by string that GDMaim renames.

    A name only matters when this project declares it: a string naming an engine
    method is safe, because the addon locks every built-in itself.
    """
    reached: set[str] = set()
    declared: set[str] = set()
    for script in _project_scripts(root, folders):
        text = script.read_text(encoding="utf-8", errors="replace")
        for pattern in (REACHED, CALLABLE):
            reached.update(match.group(1) for match in pattern.finditer(text))
        for pattern in (DECLARED, SIGNAL):
            declared.update(match.group(1) for match in pattern.finditer(text))

    builtins_file = root / "addons/gdmaim/builtins.gd"
    builtins = set(re.findall(r'"(\w+)"', builtins_file.read_text(encoding="utf-8", errors="replace"))) \
        if builtins_file.is_file() else set()

    ignore_file = root / "addons/gdmaim/user/ignore_tokens.txt"
    ignored = set()
    if ignore_file.is_file():
        ignored = {line.strip() for line in ignore_file.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#")}

    needed = sorted((reached & declared) - builtins)
    missing = sorted(set(needed) - ignored)
    stale = sorted(ignored - set(needed))
    return needed, missing, stale


def write_locks(root: Path, needed: list[str]) -> Path:
    path = root / "addons/gdmaim/user/ignore_tokens.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Symbols this project reaches through a string literal. GDMaim renames a\n"
              "# declaration and never a string, so an unlisted name breaks in the exported\n"
              "# build and nowhere else. Regenerated by: ship.py obfuscate --locks\n")
    path.write_text(header + "\n".join(needed) + "\n", encoding="utf-8")
    return path


def source_map(export_log: str) -> str | None:
    found = re.search(r"source map has been saved to '([^']+)'", export_log)
    return found.group(1).removeprefix("res://") if found else None
