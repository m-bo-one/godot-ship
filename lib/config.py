"""Two config files, and the split between them is the point.

`godot-ship.json` is tracked: what a build of THIS project contains and how it is
checked. It has no machine paths in it, ever -- that is what makes it shareable
and what the pre-commit hook enforces.

`.godot-ship.local.json` is git-ignored: where the engine, the templates and the
key live on THIS machine. Environment variables win over it, so CI needs no file
at all.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import subprocess
from pathlib import Path

from . import yamlish
from .rules import HYGIENE


@functools.lru_cache(maxsize=16)
def _version_of(engine: str) -> str:
    """`4.7.stable.official.<hash>` as the engine itself reports it."""
    try:
        out = subprocess.run([engine, "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip().splitlines()
        return out[-1].strip() if out else "?"
    except (OSError, subprocess.SubprocessError):
        return "?"

TRACKED = "godot-ship.yaml"
LOCAL = ".godot-ship.local.yaml"
# Read too, in this order, when a project already has one: `.yml`, then `.json`.
# YAML is what init writes -- the lists in here want a sentence of explanation
# each, and JSON has nowhere to put one.
TRACKED_ALTS = ["godot-ship.yml", "godot-ship.json"]
LOCAL_ALTS = [".godot-ship.local.yml", ".godot-ship.local.json"]

# The three platforms an export can be for: CLI name -> (the `platform=` value
# Godot writes in export_presets.cfg, the preset name the generator gives it, the
# artifact path when no preset names one). lib/build.py carries the same three
# rows and is not imported here on purpose -- it runs as its own process.
PLATFORMS = {
    "windows": ("Windows Desktop", "Windows Desktop", "build/{name}.exe"),
    "macos": ("macOS", "macOS", "build/{name}.zip"),
    "web": ("Web", "Web", "build/web/index.html"),
}

# Everything a project can leave unsaid. A game with no unusual needs ships with
# a five-line godot-ship.json and inherits the rest.
DEFAULTS = {
    "targets": ["windows"],
    # Named export variants of one platform: two web builds from one tree, one
    # per store, each with its own preset, output and strip list. A target with
    # no entry here is the platform itself, exactly as it always was.
    "variants": {},
    "encrypt": {},
    "obfuscate": False,
    # Where to look for symbols reached by string. "." is the whole project.
    # `exclude` names folders GDMaim must leave alone (their symbols stay locked
    # everywhere); every variant's store addon is added to it without being listed.
    "obfuscation": {"scan": ["."], "exclude": []},
    "key": ".keys/dev.gdkey",
    "boot": {
        "target": "windows",
        # Two tokens, never `--quit-after=N`: Godot reads the count as a separate
        # argument, and the `=` form is passed through to the game, which does not
        # parse it -- the run then never ends and the build hangs until it times
        # out. The count is in frames.
        "args": ["--headless", "--quit-after", "240"],
        # Name the failures; do not reject every `ERROR:`. Headless has no display
        # server, so a healthy build still prints several of those.
        "reject": ["SCRIPT ERROR", "Parse Error", "push_error", "Failed to load script",
                   "Can't open encrypted pack", "Couldn't load project data"],
        "min_pack_kb": 100,
    },
    "audit": {
        # What may never reach a player. Extend per project. This is rules.HYGIENE
        # itself, not a copy of it: the two were written out separately once and
        # drifted, and a project's own list REPLACES this one (see _merge), so a
        # shorter default here silently shrank the net for every configured project.
        "forbidden": list(HYGIENE),
        # [[alternative paths...], expected count]. Alternatives count together:
        # a scene ships as .tscn or as .godot/exported/*.scn depending on export.
        "required": [[["project.binary"], 1]],
    },
    "paths": {
        # Documentation is where a machine path belongs; config and code are not.
        "skip": ["*.md", ".godot-ship.local.json", ".claude/skills/*"],
        "allow": [],
    },
}


def outside_project(root: Path, candidate: str) -> bool:
    """Is this path somewhere other than inside the project?

    The rule it enforces: a file that travels with the repository may not name a
    binary that also travels with the repository. `engine:` is read out of the
    project directory and ship.py RUNS it -- `doctor` runs it to print a version
    -- so a project that ships both halves would be choosing what executes on a
    stranger's machine. Only $GODOT_SHIP_ENGINE, which cannot arrive in a clone
    or a zip, may name anything it likes.
    """
    try:
        return not Path(candidate).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


@dataclasses.dataclass(frozen=True)
class Variant:
    """One export: a platform, the preset it uses and what is held out of it.

    `explicit` says whether godot-ship.yaml declared it under `variants:`. A
    plain platform target keeps the older, looser preset lookup -- by name and
    then by `platform=` -- because projects predating variants name their preset
    whatever they like. A declared variant is found by name only: with two Web
    presets in the file, "the first one whose platform is Web" is a coin toss.
    """
    name: str
    platform: str          # windows | macos | web
    preset: str            # name= in export_presets.cfg
    out: str | None        # export path relative to the project, or the preset's own
    strip: dict | None     # replaces the top-level `strip` for this export; None = inherit
    archive: str | None    # a flat .zip of the export, relative to the project
    addon: str | None      # a store SDK the export needs installed first (playgama_bridge, gamepix)
    post: str | None       # a command run from the project root after the export and the archive
    explicit: bool

    @property
    def artifact(self) -> str:
        return PLATFORMS[self.platform][2]


class Config:
    def __init__(self, root: Path, tracked: dict, local: dict):
        self.root = root
        self.data = _merge(DEFAULTS, tracked)
        self.local = local
        self.refused: list[str] = []   # candidates rejected, and why -- doctor prints them

    # -- tracked ------------------------------------------------------------
    def __getitem__(self, key: str):
        return self.data[key]

    def get(self, key: str, fallback=None):
        return self.data.get(key, fallback)

    @property
    def targets(self) -> list[str]:
        return list(self.data["targets"])

    def encrypts(self, target: str) -> bool:
        """`encrypt:` is read by variant name first and by platform after, so a
        map keyed by platform keeps covering every variant built on it."""
        table = self.data["encrypt"] or {}
        if target in table:
            return bool(table[target])
        platform = self.variant(target).platform
        return bool(table.get(platform, False))

    def variant(self, target: str) -> Variant:
        """Resolve a target name: a declared variant, or a bare platform.

        Anything else is refused here, before any file is touched: a misspelt
        target that fell through to lib/build.py used to surface as argparse's
        "invalid choice" after preflight had already run.
        """
        table = self.data.get("variants") or {}
        entry = table.get(target)
        if entry is None and target in PLATFORMS:
            _, preset, _ = PLATFORMS[target]
            return Variant(target, target, preset, None, None, None, None, None, explicit=False)
        if entry is None:
            known = ", ".join([*PLATFORMS, *table])
            raise SystemExit(f"unknown target {target!r} -- not a platform and not declared "
                             f"under `variants:` in {TRACKED}. Known: {known}")
        if not isinstance(entry, dict):
            raise SystemExit(f"variants.{target} in {TRACKED} must be a map, not {entry!r}")
        platform = entry.get("platform") or (target if target in PLATFORMS else None)
        if platform not in PLATFORMS:
            raise SystemExit(f"variants.{target}.platform is {platform!r}; it must be one of "
                             + ", ".join(PLATFORMS))
        strip = entry.get("strip")
        if strip is not None and not isinstance(strip, dict):
            raise SystemExit(f"variants.{target}.strip must be a map with `autoloads` and/or "
                             "`plugins` lists")
        return Variant(
            name=target,
            platform=platform,
            preset=str(entry.get("preset") or PLATFORMS[platform][1]),
            out=str(entry["out"]) if entry.get("out") else None,
            strip=strip,
            archive=str(entry["archive"]) if entry.get("archive") else None,
            addon=str(entry["addon"]) if entry.get("addon") else None,
            post=str(entry["post"]) if entry.get("post") else None,
            explicit=True,
        )

    def variants(self, targets: list[str] | None = None) -> list[Variant]:
        return [self.variant(t) for t in (targets if targets is not None else self.targets)]

    def strip_for(self, variant: Variant) -> dict:
        """The variant's own `strip` REPLACES the top-level one -- not merged.
        Merging would make it impossible for one variant to keep an autoload the
        others drop, which is the whole reason a variant has its own list."""
        rules = variant.strip if variant.strip is not None else (self.data.get("strip") or {})
        return {"autoloads": list(rules.get("autoloads") or []),
                "plugins": list(rules.get("plugins") or [])}

    # -- local, never committed ---------------------------------------------
    def engine_candidates(self) -> list[tuple[str, str]]:
        """(path, where it came from), best first.

        `GODOT_SHIP_ENGINE` is ours and explicit, so it wins. The local file
        comes next and the generic variables after it -- deliberately: `GODOT`
        and `GODOT_EXE` tend to be set machine-wide to whatever build was
        installed last, and a project pinned to one engine version must not be
        hijacked by it. CI still overrides everything with the explicit one.
        """
        found = []
        self.refused = []
        explicit = os.environ.get("GODOT_SHIP_ENGINE")
        if explicit:
            found.append((explicit, "$GODOT_SHIP_ENGINE"))
        named = self.local.get("engine")
        if named and not outside_project(self.root, named):
            self.refused.append(
                f"{LOCAL} names {named}, which is inside the project -- refused, because "
                "that is the repository choosing which binary ship.py runs. If it really "
                "is your engine, say so from outside the repository: set $GODOT_SHIP_ENGINE.")
        elif named:
            found.append((named, LOCAL))
        for var in ("GODOT", "GODOT_EXE", "GODOT_BIN"):
            if os.environ.get(var):
                found.append((os.environ[var], f"${var}"))
        return [(p, src) for p, src in found if Path(p).is_file()]

    @property
    def engine(self) -> str | None:
        """The first candidate whose version matches `engine_version`, if the
        project pins one. A mismatch is skipped rather than accepted and blamed
        later: the export against a wrong-version template fails without a reason."""
        want = self.data.get("engine_version")
        candidates = self.engine_candidates()
        for path, _source in candidates:
            if not want or _version_of(path).startswith(want):
                return path
        return None

    def engine_report(self) -> list[tuple[str, str, str]]:
        """(path, source, version) for every candidate -- what `doctor` prints."""
        return [(p, s, _version_of(p)) for p, s in self.engine_candidates()]

    def template(self, target: str) -> str | None:
        """The custom export template for a target, or None for the official one.

        Never read from the tracked config: this is an absolute path on one
        machine, and writing it into export_presets.cfg is what the hook refuses.

        Looked up by the variant's name and then by its platform, in both the
        environment and the local file, so `template: {web: ...}` covers every
        web variant and one of them can still be pointed at a template of its own.
        """
        variant = self.variant(target)
        names = [variant.name] if variant.name == variant.platform \
            else [variant.name, variant.platform]
        table = self.local.get("template") or {}
        for name in names:
            env = os.environ.get(f"GODOT_TEMPLATE_{name.upper()}")
            if env and Path(env).is_file():
                return env
        for name in names:
            found = table.get(name)
            if found and Path(found).is_file():
                return found
        return None

    def key(self) -> str | None:
        env = os.environ.get("GODOT_SCRIPT_ENCRYPTION_KEY")
        if env:
            return env.strip()
        path = self.root / self.local.get("key", self.data["key"])
        if path.is_file():
            return path.read_text(encoding="ascii").strip()
        return None

    def key_path(self) -> Path:
        return self.root / self.local.get("key", self.data["key"])


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def find_project(start: Path) -> Path:
    for folder in [start, *start.parents]:
        if (folder / "project.godot").is_file():
            return folder
    raise SystemExit(f"no project.godot in {start} or any parent directory")


def _read(root: Path, primary: str, alternatives: list[str]) -> dict:
    for name in [primary, *alternatives]:
        file = root / name
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8")
        if name.endswith(".json"):
            return json.loads(text)
        return yamlish.loads(text) or {}
    return {}


def _normalise(data: dict) -> dict:
    """`required` is written as a list of maps, which reads far better than
    JSON's [[["a","b"], 1]]. Both arrive here as [(patterns, count)].

        audit:
          required:
            - paths: ["scenes/main.tscn", ".godot/exported/*-main.scn"]
              count: 1
    """
    audit = data.get("audit")
    if not isinstance(audit, dict):
        return data
    required = audit.get("required")
    if isinstance(required, list) and required and isinstance(required[0], dict):
        audit["required"] = [[entry.get("paths", []), int(entry.get("count", 1))]
                             for entry in required]
    return data


def which_files(root: Path) -> tuple[str | None, str | None]:
    """The config files this project actually has, or None where it has none."""
    def first(primary: str, alternatives: list[str]) -> str | None:
        for name in [primary, *alternatives]:
            if (root / name).is_file():
                return name
        return None
    return first(TRACKED, TRACKED_ALTS), first(LOCAL, LOCAL_ALTS)


def is_tracked(root: Path, name: str) -> bool:
    """Is this path committed to the repository?

    The only shell-out in this file, and it earns its place: the local config
    names the engine binary that ship.py RUNS, so whether that file came from
    this machine or arrived with somebody's clone is a different question from
    whether it parses.
    """
    try:
        done = subprocess.run(["git", "ls-files", "--error-unmatch", "--", name],
                              cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False        # no git, or no repository: nothing is tracked
    return done.returncode == 0


def load(root: Path) -> Config:
    tracked = _normalise(_read(root, TRACKED, TRACKED_ALTS))
    local = _read(root, LOCAL, LOCAL_ALTS)
    local_name = which_files(root)[1]
    # A file called `.local` that arrived with the repository is not local. It
    # names the engine ship.py executes and the templates it trusts, and `doctor`
    # -- a command whose own help says it changes nothing -- runs that engine to
    # read its version. Refuse the whole run rather than quietly obeying it.
    if local and local_name and is_tracked(root, local_name):
        raise SystemExit(
            f"{local_name} is committed to this repository.\n"
            "  That file is meant to describe THIS machine, and it names the binary\n"
            "  ship.py executes -- a copy that came with a clone must not be trusted.\n"
            f"  Fix it with:  git rm --cached {local_name}\n"
            f"  then check it is ignored, and re-run. Delete it and `ship.py init` to\n"
            "  write a fresh one for this machine.")
    return Config(root, tracked, local)
