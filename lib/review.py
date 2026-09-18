"""Audit the build setup, not just the build.

`audit --check` answers "is anything on the forbidden list inside the pack".
This answers the harder question: **what is not on the list that should be**, and
what the finished artifact gives away. Both failures are silent -- a clean
`--check` over an incomplete list reads exactly like a clean build.

Everything here is read-only.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# Every table this module matches against lives in rules.py -- one file to edit
# when the shape of the junk changes, instead of a regex per module.
from .rules import ANY_HOME, LEAKS, NEVER_FLAG, RESOURCE_EXT, SUSPECT


def compute_shaders(root: Path) -> list[str]:
    """Files that make a web target pointless. See rules.COMPUTE_SHADERS."""
    from .rules import COMPUTE_SHADERS
    found: list[str] = []
    for glob in COMPUTE_SHADERS:
        found += [p.relative_to(root).as_posix() for p in root.glob(glob)
                  if p.is_file() and ".godot" not in p.parts]
    return sorted(found)


def editor_addons(root: Path) -> list[str]:
    """Addons carrying an EditorPlugin: their code is for the editor, and a copy
    of it in a player's build is at best dead weight and at worst a debug channel."""
    found = []
    addons = root / "addons"
    if not addons.is_dir():
        return found
    for folder in sorted(p for p in addons.iterdir() if p.is_dir()):
        config = folder / "plugin.cfg"
        if not config.is_file():
            continue
        text = config.read_text(encoding="utf-8", errors="replace")
        script = re.search(r'script="([^"]+)"', text)
        if script:
            found.append(f"addons/{folder.name}")
    return found


def uncovered(root: Path, forbidden: list[str], exclude_filter: str) -> list[tuple[str, str]]:
    """Top-level entries that no forbidden pattern and no exclude filter names."""
    patterns = list(forbidden) + [p.strip() for p in exclude_filter.split(",") if p.strip()]
    out = []
    for entry in sorted(root.iterdir()):
        if entry.name in NEVER_FLAG:
            continue
        name = entry.name + ("/" if entry.is_dir() else "")
        probe = entry.name + ("/x" if entry.is_dir() else "")
        if any(fnmatch.fnmatch(probe, p) or fnmatch.fnmatch(entry.name, p) for p in patterns):
            continue
        if entry.name.startswith("."):
            # A dot-entry is only a problem when something inside it can ship.
            shippable = entry.suffix.lower() in RESOURCE_EXT if entry.is_file() else any(
                child.suffix.lower() in RESOURCE_EXT for child in entry.rglob("*")
                if child.is_file())
            if shippable:
                out.append((name, "Godot skips hidden DIRECTORIES but not hidden FILES, "
                                  "and this one holds something the exporter treats as a resource"))
            continue
        for rule, why in SUSPECT:
            if rule.match(entry.name):
                out.append((name, why))
                break
    return out


def autoloads(root: Path) -> list[tuple[str, str]]:
    """(name, path) from project.godot -- an autoload from a dev addon is a
    debugger channel in a stranger's game, and excluding its files is not enough:
    the autoload survives and the first frame is three ERROR lines."""
    if not (root / "project.godot").is_file():
        return []
    manifest = (root / "project.godot").read_text(encoding="utf-8", errors="replace")
    section = re.search(r"(?ms)^\[autoload\]\s*(.*?)(?=^\[|\Z)", manifest)
    if not section:
        return []
    found = []
    for line in section.group(1).splitlines():
        match = re.match(r'^(\w+)\s*=\s*"\*?(res://[^"]+)"', line.strip())
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def excluded(path: str, exclude_filter: str) -> bool:
    """Does the preset's exclude_filter drop this res:// path? Godot strips the
    scheme and glob-matches each comma-separated pattern, `*` crossing `/`."""
    bare = path.removeprefix("res://")
    return any(fnmatch.fnmatch(bare, pattern.strip())
               for pattern in exclude_filter.split(",") if pattern.strip())


def halves(root: Path, exclude_filter: str, strip: dict) -> tuple[list[str], list[str]]:
    """(fails, warnings) for one export: both halves of a dev addon or neither.

    The failure: an autoload this export keeps whose script its preset excludes.
    The pack has no script at that path, the autoload survives in project.godot,
    and a player's first frame is three ERROR lines. The warning is the reverse:
    an autoload this export drops whose files still ship -- dead weight rather
    than a broken start, but the addon's code is in a stranger's build for
    nothing -- and a plugin dropped from `[editor_plugins]` while its autoload
    stays, which the plugin puts straight back when the editor next loads.
    """
    fails: list[str] = []
    warnings: list[str] = []
    dropped = set(strip.get("autoloads") or [])
    plugins = [p.removeprefix("res://") for p in strip.get("plugins") or []]
    for name, path in autoloads(root):
        bare = path.removeprefix("res://")
        if name in dropped and not excluded(path, exclude_filter):
            warnings.append(f"strips autoload {name} but its preset does not exclude {bare} -- "
                            "the script ships without the autoload that used it")
        elif name not in dropped and excluded(path, exclude_filter):
            fails.append(f"keeps autoload {name} while its preset excludes {bare} -- "
                         "the autoload survives in project.godot pointing at nothing, and "
                         "the first frame is ERROR lines. Add it to this export's strip.autoloads")
        # A plugin is dropped so the export runs without it; the autoload the
        # plugin registers has to go in the same breath, or the plugin's own
        # _enter_tree writes it back the next time the editor loads the project.
        folder = str(Path(bare).parent.as_posix())
        for plugin in plugins:
            if name not in dropped and Path(plugin).parent.as_posix() == folder:
                warnings.append(f"strips plugin {plugin} and keeps its autoload {name} -- "
                                "the plugin re-registers it on the next editor load")
    return fails, warnings


def leaks(artifact: Path, key: str | None) -> tuple[list[tuple[str, int]], list[str]]:
    """(hard findings, remarks) for what is readable in the artifact.

    The pack key is looked for by its own bytes: inside the PACK it unlocks the
    pack. Inside the exe it is there by design -- the game has to read its own
    pack -- so that is a remark, and an honest one: it means the encryption stops
    the curious, not the determined.
    """
    data = artifact.read_bytes()
    found: list[tuple[str, int]] = []
    remarks: list[str] = []
    for pattern, what in LEAKS:
        match = pattern.search(data)
        if match:
            found.append((what, match.start()))

    home = Path.home()
    mine = re.compile(re.escape(home.name).encode(), re.I)
    others = ANY_HOME
    for match in others.finditer(data):
        user = match.group(1)
        if mine.fullmatch(user):
            found.append((f"this machine's home directory ({home.name})", match.start()))
            break
    else:
        names = {m.group(1).decode("latin-1") for m in others.finditer(data)}
        if names:
            remarks.append("carries build-machine paths of whoever compiled the engine "
                           f"({', '.join(sorted(names)[:3])}) -- upstream's, not yours")

    if key:
        raw = bytes.fromhex(key)
        if raw in data:
            found.append(("the pack encryption key itself", data.find(raw)))
        if key.lower().encode() in data.lower():
            found.append(("the pack key as hex text", data.lower().find(key.lower().encode())))
    return found, remarks
