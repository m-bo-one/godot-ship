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
# The lock list GDMaim reads when the project keeps one of its own; without it,
# the one inside the addon (LEGACY_LOCKS).
PROJECT_LOCKS = ".gdmaim/ignore_tokens.txt"
LEGACY_LOCKS = "addons/gdmaim/user/ignore_tokens.txt"


def linked(root: Path) -> bool:
    """True when addons/gdmaim is a link (a junction or a symlink) to a checkout that
    other projects share: the export.cfg and lock list inside it are not this project's."""
    folder = root / "addons/gdmaim"
    return folder.is_symlink() or bool(getattr(folder, "is_junction", lambda: False)())


def config_files(root: Path) -> list[str]:
    """CONFIG_FILES this project owns: the addon's own export.cfg is left out when the
    addon is linked in and the project keeps its lock list in .gdmaim/ (it is another
    project's file then; GDMaim reads .gdmaim/export.cfg first anyway)."""
    shared = linked(root) and (root / PROJECT_LOCKS).is_file()
    return [name for name in CONFIG_FILES if not (name.startswith("addons/") and shared)]


def lock_path(root: Path) -> Path:
    """The project's own .gdmaim/ignore_tokens.txt when it has one (GDMaim reads that
    one first), else the list inside the addon. Never chosen for a project that has
    none: a project that tracks the list inside a linked addon keeps it there."""
    own = root / PROJECT_LOCKS
    return own if own.is_file() else root / LEGACY_LOCKS

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
        # Stripping @export makes an inherited scene lose every typed node
        # reference its base scene set (`visual = NodePath(...)` on a child
        # scene resolves against the annotation), so the child starts with
        # null exports and its _ready dies. Found on a lucky block that
        # inherits a block; the annotations cost nothing in the pack.
        "strip_editor_annotations": "false",
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
    "exclude_files_category": {
        # The lock list is read ONLY behind this flag. Off, ignore_tokens.txt is a
        # file nobody opens, and every string-reached symbol ships renamed while
        # the scan reports them all locked. Found on a Bridge build whose game
        # never called the SDK once.
        "custom_tokens_enabled": "true",
        # `multi_filepath` is written per call from `exclude_paths()`.
    },
}

EXCLUDE_SECTION = "exclude_files_category"
EXCLUDE_KEY = "multi_filepath"


def exclude_paths(folders: list[str]) -> str:
    """The `;`-joined res:// list GDMaim's exclusion field holds.

    A store SDK addon is reached from two sides GDMaim cannot see: the game
    names its members in strings (`call("set_score")`, `get("platform")`) and
    the platform's JavaScript names them from outside the pack. Renaming any of
    them breaks the SDK silently, so the whole folder is excluded; GDMaim then
    locks every symbol those scripts declare, project-wide.
    """
    seen: list[str] = []
    for folder in folders:
        clean = folder.strip().replace("\\", "/").removeprefix("res://").strip("/")
        if clean and clean not in seen:
            seen.append(clean)
    return ";".join(f"res://{folder}/" for folder in seen)

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
# A *property* named in a string. Only the shapes a Dictionary does not share: bare `get`/`set`
# would drag in every `row.get("id")` key that happens to match a member -- measured at 70 extra
# locks on one project -- so those stay manual. First path segment only: "modulate:a" is `modulate`.
PROPERTY = re.compile(
    r"(?:\.|(?<![A-Za-z0-9_]))"
    r"(?:tween_property|tween_method|set_deferred|get_indexed|set_indexed)"
    r"\(\s*[^,()]*,?\s*&?\"([A-Za-z_]\w*)(?::[\w:]+)?\"")
# ...and what declares one. Without this a `var` reached by string can never be found: the old
# scan intersected with `func`/`signal` alone, so a tweened property was invisible by construction.
DECLARED_VAR = re.compile(r"(?m)^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?var\s+(\w+)")
# Everything below this line in the lock list is a human's, and regeneration re-emits it verbatim.
MANUAL_MARK = "# --- MANUAL: kept across regeneration. Add the call site with the name. ---"


def installed(root: Path) -> bool:
    return (root / "addons/gdmaim/plugin.cfg").is_file()


def install(root: Path, source: str | None) -> None:
    """Copy the addon in from a local checkout, or clone it."""
    target = root / "addons/gdmaim"
    if installed(root):
        print("  addons/gdmaim already there")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # The folder usually exists already: a project tracks the addon's export.cfg and
    # ignore_tokens.txt while gitignoring the addon itself, so a fresh worktree has
    # `addons/gdmaim/` with no plugin.cfg in it and copytree onto it raised
    # FileExistsError -- in exactly the situation the message said to run this.
    # dirs_exist_ok makes copytree OVERWRITE, so the project's own settings and lock
    # list are kept out of the copy: a source tree carrying its own export.cfg or
    # user/ would otherwise replace them silently at install time.
    keep_out = shutil.ignore_patterns(".git", "__pycache__", "export.cfg", "user")
    if source and (Path(source) / "addons/gdmaim").is_dir():
        shutil.copytree(Path(source) / "addons/gdmaim", target, ignore=keep_out,
                        dirs_exist_ok=True)
        print(f"  addons/gdmaim <- {source}")
        return
    if source and (Path(source) / "plugin.cfg").is_file():
        shutil.copytree(Path(source), target, ignore=keep_out, dirs_exist_ok=True)
        print(f"  addons/gdmaim <- {source}")
        return
    print(f"  cloning {UPSTREAM}")
    temporary = root / ".gdmaim-clone"
    subprocess.run(["git", "clone", "--depth", "1", UPSTREAM, str(temporary)], check=True)
    shutil.copytree(temporary / "addons/gdmaim", target, ignore=keep_out, dirs_exist_ok=True)
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


def write_settings(root: Path, exclude: list[str] | None = None) -> None:
    """The settled settings into both files, and the exclusion list into them.

    The list written is the union of what the config asks for and what the
    file already held: a folder somebody excluded by hand in the editor -- a
    vendored library reached by string -- is not on the tracked list, and
    replacing the field would drop it without a word on the next
    `ship.py obfuscate`, which is a renamed symbol found only in the exported
    build. What was kept from the file is printed, so it can be moved into
    `obfuscation.exclude` where it belongs.
    """
    wanted = exclude_paths(exclude or [])
    kept = [p for p in _excluded_now(root) if p not in wanted.split(";")]
    joined = ";".join(part for part in [wanted, *kept] if part)
    settings = {section: dict(values) for section, values in SETTINGS.items()}
    settings[EXCLUDE_SECTION][EXCLUDE_KEY] = f'"{joined}"'
    for name in config_files(root):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(_merged(existing, settings), encoding="utf-8")
    print(f"  settings written to {' and '.join(config_files(root))}")
    if exclude:
        print(f"  left unobfuscated: {', '.join(exclude)}")
    if kept:
        print(f"  kept from the file, not in obfuscation.exclude: {', '.join(kept)}")


def _excluded_now(root: Path) -> list[str]:
    """Every res:// path the files' `multi_filepath` holds today, in order."""
    found: list[str] = []
    for name in config_files(root):
        path = root / name
        if not path.is_file():
            continue
        section = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            header = re.match(r"^\[(\w+)\]$", line.strip())
            if header:
                section = header.group(1)
                continue
            key = re.match(rf"^{EXCLUDE_KEY}=(.*)$", line.strip())
            if section == EXCLUDE_SECTION and key:
                for part in key.group(1).strip().strip('"').split(";"):
                    if part.strip() and part.strip() not in found:
                        found.append(part.strip())
    return found


def review(root: Path, exclude: list[str]) -> list[str]:
    """What is wrong with the GDMaim config on disk, in sentences.

    Both files are read because GDMaim reads them in order and the second wins
    for a key the first lacks; a value fixed in one of them is still the old
    value if the other one carries it too.
    """
    wanted = exclude_paths(exclude)
    remarks: list[str] = []
    for name in config_files(root):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        section = ""
        flag = ""
        paths = ""
        for line in text.splitlines():
            header = re.match(r"^\[(\w+)\]$", line.strip())
            if header:
                section = header.group(1)
                continue
            if section != EXCLUDE_SECTION:
                continue
            key = re.match(r"^(\w+)=(.*)$", line.strip())
            if not key:
                continue
            if key.group(1) == "custom_tokens_enabled":
                flag = key.group(2).strip()
            elif key.group(1) == EXCLUDE_KEY:
                paths = key.group(2).strip().strip('"')
        if flag != "true":
            remarks.append(f"{name}: custom_tokens_enabled is {flag or 'unset'} -- the lock list "
                           "is never read and every string-reached symbol ships renamed. "
                           "Run: ship.py obfuscate")
        missing = [p for p in wanted.split(";") if p and p not in paths.split(";")]
        if missing:
            remarks.append(f"{name}: not excluded from obfuscation: {', '.join(missing)} -- a "
                           "store SDK the game reaches by string is renamed out from under it. "
                           "Run: ship.py obfuscate")
    return remarks


def _merged(existing: str, settings: dict[str, dict[str, str]] | None = None) -> str:
    """Keep whatever the addon put there, override only what is settled."""
    table = settings or SETTINGS
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
        if key and section in table and key.group(1) in table[section]:
            name = key.group(1)
            seen[section].add(name)
            out.append(f"{name}={table[section][name]}")
            continue
        out.append(line)
    for section, values in table.items():
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
    reached_prop: set[str] = set()
    declared_var: set[str] = set()
    for script in _project_scripts(root, folders):
        text = script.read_text(encoding="utf-8", errors="replace")
        for pattern in (REACHED, CALLABLE):
            reached.update(match.group(1) for match in pattern.finditer(text))
        for pattern in (DECLARED, SIGNAL):
            declared.update(match.group(1) for match in pattern.finditer(text))
        reached_prop.update(match.group(1) for match in PROPERTY.finditer(text))
        declared_var.update(match.group(1) for match in DECLARED_VAR.finditer(text))

    builtins_file = root / "addons/gdmaim/builtins.gd"
    builtins = set(re.findall(r'"(\w+)"', builtins_file.read_text(encoding="utf-8", errors="replace"))) \
        if builtins_file.is_file() else set()

    ignored = set(read_locks(root))
    # The manual block is a human's answer to what no scan can see, so it is never `stale`.
    manual = set(manual_locks(root))

    # A property counts only when this project declares a `var` of that name: `row.get("id")` on a
    # Dictionary names a key and not a member, and intersecting with the declarations drops it.
    needed = sorted(((reached & declared) | (reached_prop & declared_var)) - builtins)
    missing = sorted(set(needed) - ignored - manual)
    stale = sorted(ignored - set(needed) - manual)
    return needed, missing, stale


def _lock_lines(root: Path) -> list[str]:
    path = lock_path(root)
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def manual_locks(root: Path) -> list[str]:
    """Every name below the manual marker: the residue no scan can reach."""
    lines = _lock_lines(root)
    if MANUAL_MARK not in lines:
        return []
    return [line.strip() for line in lines[lines.index(MANUAL_MARK) + 1:]
            if line.strip() and not line.startswith("#")]


def manual_block(root: Path) -> list[str]:
    """...and those lines verbatim, comments included, so regeneration can put them back."""
    lines = _lock_lines(root)
    return lines[lines.index(MANUAL_MARK) + 1:] if MANUAL_MARK in lines else []


def read_locks(root: Path) -> list[str]:
    return [line.strip() for line in _lock_lines(root)
            if line.strip() and not line.startswith("#")]


def write_locks(root: Path, needed: list[str]) -> Path:
    """The scan's names, then the manual block verbatim. Dropping that block was how a
    hand-locked name vanished on the next regeneration and broke only the exported build."""
    path = lock_path(root)
    kept = manual_block(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join([
        "# Symbols this project reaches through a string literal. GDMaim renames a",
        "# declaration and never a string, so an unlisted name breaks in the exported",
        "# build and nowhere else. Regenerated by: ship.py obfuscate --locks",
        "# Everything below the MANUAL marker is kept exactly as it stands.",
    ]) + "\n"
    out = header + "\n".join(needed) + "\n\n" + MANUAL_MARK + "\n"
    if kept:
        out += "\n".join(kept) + "\n"
    path.write_text(out, encoding="utf-8")
    return path


def source_map(export_log: str) -> str | None:
    found = re.search(r"source map has been saved to '([^']+)'", export_log)
    return found.group(1).removeprefix("res://") if found else None
