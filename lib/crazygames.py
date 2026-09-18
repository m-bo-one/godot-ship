"""CrazyGames: the store's own Godot addon, which needs no shell and no preset
of a special name -- and nothing about it can be downloaded by a script.

Read off the addon itself (`author="CrazyGames"`; plugin.cfg says 1.0.0, the
wrapper reports 1.0.1 to the SDK) and the store's docs
(docs.crazygames.com/sdk/intro, the Godot tabs; requirements/technical):

- **Two autoloads, in this order**: `CrazyGamesBridge`, then `CrazyGames`.
  `CrazyGames._ready` connects to `CrazyGamesBridge.callbacks` and calls
  `CrazyGamesBridge.init_sdk`, so with the order reversed -- which is what a
  hand-added pair often is -- the second is a null instance on the first frame.
  Both go at the top of [autoload]; both come out of every other web build.
- **The folder is `addons/crazygames`.** The plugin script (`sdk_load.gd`)
  registers `res://addons/crazygames/CrazyGames.gd` by that literal path.
- **No HTML shell.** `CrazyGamesBridge.init_sdk` appends the
  `crazygames-sdk-v3.js` script tag to <head> at run time, so the preset keeps
  Godot's own shell and `SHELL` is empty. "The CrazyGames export preset" in the
  docs is just a web preset of the project's; the plugin writes none.
- **The archive holds two addons**, `crazysdk-godot-3` and `crazysdk-godot-4`,
  side by side. The Godot 3 one in a 4.x project is a wall of parse errors on
  load (`yield`), and it sorts first -- hence `pick`.
- **No script can fetch it.** The only published source is the Godot Asset Store
  page; it answers a non-browser with 403, and the store's API does not list
  the asset. `download()` therefore says where a person gets the zip, and
  `crazygames_src` in the local config is how it gets in.

What the store holds a build to, and the tool can see (requirements/technical):
at most 1500 files and 250 MB in all; an initial download -- everything fetched
before the game calls `CrazyGames.Game.gameplay_start()` -- of at most 50 MB,
and 20 MB to be eligible for the mobile homepage; the exported page named
`index.html`; and `user-select: none` on the body, without which a long press
on a tablet selects the whole game and opens a context menu. A Godot web export
fetches its .wasm and .pck whole before the first frame, so the export's size
IS its initial download unless the project streams packs itself.

There is a second road to this store: CrazyGames is also a platform inside
Playgama Bridge (`crazy_games`, picked by hostname), so a project already on
Bridge can upload its Playgama build instead. It is one or the other -- a build
carries exactly the SDK it names.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import sdk

KEY = "crazygames"
COMMAND = "crazygames"
VARIANT = "crazygames"

STORE = "https://store.godotengine.org/asset/crazygames/crazysdk"
DOCS = "https://docs.crazygames.com/sdk/intro/#godot_1"

ADDON = "addons/crazygames"
PLUGIN = "res://addons/crazygames/plugin.cfg"
# Load order, not alphabetical: the second is built on the first.
AUTOLOADS = [
    ("CrazyGamesBridge", "*res://addons/crazygames/Utils/CrazyGamesBridge.gd"),
    ("CrazyGames", "*res://addons/crazygames/CrazyGames.gd"),
]
AUTOLOAD = "CrazyGames"
AUTOLOAD_PATH = AUTOLOADS[-1][1]
SHELL = ""
EXCLUDE = "addons/crazygames/*"

PRESET = "CrazyGames"
OUT = "build/crazygames/web/index.html"

GODOT4_FOLDER = "crazysdk-godot-4"

MAX_FILES = 1500
MAX_TOTAL_MB = 250
MAX_INITIAL_MB = 50
MOBILE_INITIAL_MB = 20

# On the body, per requirements/technical. No quotes inside: it is appended to a
# value that export_presets.cfg already holds between double quotes.
NO_SELECT = ("<style>body{-webkit-user-select:none;-moz-user-select:none;"
             "-ms-user-select:none;user-select:none}</style>")


def download() -> tuple[str, bytes]:
    raise SystemExit(
        "the CrazyGames Godot SDK cannot be downloaded by a script: its only source is the\n"
        f"  Godot Asset Store, which refuses a non-browser. Download it by hand from\n    {STORE}\n"
        f"  (linked from {DOCS}) and name the zip -- or the unpacked folder -- in\n"
        f"  .godot-ship.local.yaml as {KEY}_src.")


def pick(found: list[Path]) -> Path:
    """The Godot 4 addon out of an archive that also holds the Godot 3 one."""
    if len(found) == 1:
        return found[0]
    for path in found:
        if path.parent.name == GODOT4_FOLDER:
            return path
    names = ", ".join(path.parent.name for path in found)
    raise SystemExit(f"the CrazyGames archive holds {names} and no {GODOT4_FOLDER} -- this tool "
                     "exports Godot 4 projects; point crazygames_src at the Godot 4 addon folder")


def installed(root: Path) -> bool:
    return sdk.installed(root, _SELF)


def version(root: Path) -> str:
    return sdk.version(root, _SELF)


def install(root: Path, source: str | None) -> None:
    sdk.install(root, _SELF, source)
    if sdk.installed(root, _SELF) and not (root / ADDON / "Utils/CrazyGamesBridge.gd").is_file():
        # A third-party "CrazyGames SDK" addon under the same folder name exists, with
        # other file names; registering THESE autoload paths over it points at nothing.
        raise SystemExit(f"{ADDON} is installed but holds no Utils/CrazyGamesBridge.gd -- it is "
                         f"not the official addon from {STORE}")


def enable(root: Path) -> None:
    sdk.enable(root, _SELF)


def split_presets(root: Path, preset: str, out: str, others=()) -> list[str]:
    done = sdk.split_presets(root, _SELF, preset, out, others)
    if any("added ->" in line for line in done) and _no_select(root, preset):
        done.append(f'"{preset}": user-select:none on the body, via html/head_include -- '
                    "a long press on a tablet otherwise selects the whole game")
    return done


def _blocks(text: str):
    starts = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        body = text[match.start():end]
        name = re.search(r'^name="([^"]*)"$', body, flags=re.M)
        yield match.start(), end, body, name.group(1) if name else ""


def _no_select(root: Path, preset: str) -> bool:
    """Append the CSS to the head include of the preset this tool just wrote.
    Only there: a preset that was already in the file is the project's to edit."""
    path = root / "export_presets.cfg"
    text = path.read_text(encoding="utf-8")
    for start, end, body, name in _blocks(text):
        if name != preset or "user-select" in body:
            continue
        line = re.search(r'^html/head_include="(.*)"$', body, flags=re.M)
        if line:
            body = body[:line.start()] + f'html/head_include="{line.group(1)}{NO_SELECT}"' \
                + body[line.end():]
        else:
            body = body.rstrip("\n") + f'\nhtml/head_include="{NO_SELECT}"\n'
        path.write_text(text[:start] + body + text[end:], encoding="utf-8")
        return True
    return False


def review(root: Path, presets_text: str) -> list[str]:
    """What the store's QA will say about the preset, said first. Read-only."""
    warnings = []
    for _, _, body, name in _blocks(presets_text):
        if name != PRESET:
            continue
        shell = re.search(r'^html/custom_html_shell="([^"]*)"$', body, flags=re.M)
        if "user-select" not in body and not (shell and shell.group(1)):
            warnings.append(f'preset "{name}": no user-select:none on the body. CrazyGames asks '
                            "for it on mobile -- a long press selects the whole game. Add to "
                            f"html/head_include: {NO_SELECT}")
        out = re.search(r'^export_path="([^"]*)"$', body, flags=re.M)
        if out and out.group(1) and Path(out.group(1)).name != "index.html":
            warnings.append(f'preset "{name}" exports {Path(out.group(1)).name}: the store serves '
                            "the page as index.html, and a Godot export names its .js, .wasm "
                            "and .pck after the html -- export as index.html, do not rename")
    return warnings


def check_export(folder: Path) -> tuple[list[str], list[str]]:
    """(fails, warnings) for an exported folder, against the store's hard limits."""
    files = [path for path in folder.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files) / 1048576
    fails, warnings = [], []
    if len(files) > MAX_FILES:
        fails.append(f"{len(files)} files, and CrazyGames takes at most {MAX_FILES}")
    if total > MAX_TOTAL_MB:
        fails.append(f"{total:.0f} MB, and CrazyGames takes at most {MAX_TOTAL_MB} MB in all")
    elif total > MAX_INITIAL_MB:
        warnings.append(f"{total:.0f} MB: the initial download -- everything fetched before "
                        f"gameplay_start() -- must be at most {MAX_INITIAL_MB} MB, and a Godot "
                        "export fetches its .wasm and .pck whole before the first frame")
    elif total > MOBILE_INITIAL_MB:
        warnings.append(f"{total:.1f} MB on disk: over {MOBILE_INITIAL_MB} MB of initial download "
                        "the game is not eligible for CrazyGames' mobile homepage. The store "
                        "measures bytes downloaded, so what its CDN compresses counts for less "
                        "-- but do not count on a .pck being compressed at all")
    return fails, warnings


import sys as _sys  # noqa: E402
_SELF = _sys.modules[__name__]
