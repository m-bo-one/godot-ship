"""GamePix: a store with a Godot plugin of its own, and a plugin with opinions.

GamePix is not a platform inside Playgama Bridge. Its plugin is three files --
an autoload `GPX` wrapping `window.GamePix`, an HTML shell that loads
`gamepix.sdk.js` as the first script in <head>, and a plugin.gd -- and its QA
refuses a build carrying anyone else's SDK: "games must either use the GamePix
SDK or be entirely SDK-free". So its variant strips every other store's
autoload and plugin, and every other web variant strips this one.

Three names are not ours to choose, because plugin.gd hardcodes them:

- the folder is `addons/gpx-godot-plugin` -- the shell path is written into the
  plugin as `res://addons/gpx-godot-plugin/index.html`, so the addon unpacked
  under any other name registers and then exports with Godot's default shell
  and no SDK, without a line of error;
- the autoload is `GPX`;
- **the preset's name must begin with `GamePix`.** On every editor start -- a
  headless export included -- plugin.gd loads export_presets.cfg, and when no
  preset name begins with "GamePix" it appends one of its own and saves the
  whole file back through ConfigFile: `export_filter="all_resources"`, an EMPTY
  exclude_filter, no custom_template keys, only the shell set. A project that
  called its preset "Web GamePix" therefore grows a second preset that ships
  docs/, tools/ and every dev addon, and that second one is the one named
  exactly "GamePix". `bare_presets` is what `review` reads it with.

There is no release API: the archive URL is a constant from the docs page, and
the day it moves the error says where to get the zip by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import sdk

KEY = "gamepix"
COMMAND = "gamepix"
VARIANT = "gamepix"

DOCS = "https://my.gamepix.com/sdk/doc/godot-plugin"
ARCHIVE = "https://integration.gamepix.com/sdk/plugins/godot/2025/gpx-godot-plugin-1.2r.zip"

ADDON = "addons/gpx-godot-plugin"
PLUGIN = "res://addons/gpx-godot-plugin/plugin.cfg"
AUTOLOAD = "GPX"
AUTOLOAD_PATH = "*res://addons/gpx-godot-plugin/gpx.gd"
SHELL = "res://addons/gpx-godot-plugin/index.html"
EXCLUDE = "addons/gpx-godot-plugin/*"

# Exactly this, or at least beginning with it -- see the module docstring.
PRESET = "GamePix"
PRESET_PREFIX = "GamePix"
OUT = "build/gamepix/web/index.html"


def download() -> tuple[str, bytes]:
    print(f"  downloading {ARCHIVE}")
    try:
        return ARCHIVE.rsplit("/", 1)[-1], sdk.fetch(ARCHIVE)
    except OSError as why:
        raise SystemExit(f"could not download {ARCHIVE}: {why}\n  The archive has no stable "
                         f"address. Get the current zip from {DOCS}\n  and name it in "
                         f".godot-ship.local.yaml as {KEY}_src.")


def installed(root: Path) -> bool:
    return sdk.installed(root, _SELF)


def version(root: Path) -> str:
    return sdk.version(root, _SELF)


def install(root: Path, source: str | None) -> None:
    sdk.install(root, _SELF, source)


def enable(root: Path) -> None:
    sdk.enable(root, _SELF)


def split_presets(root: Path, preset: str, out: str, others=()) -> list[str]:
    if not preset.startswith(PRESET_PREFIX):
        raise SystemExit(f'the GamePix preset is named "{preset}", and its name must begin with '
                         f'"{PRESET_PREFIX}": the plugin appends an unfiltered preset of its '
                         "own whenever no preset name does -- see lib/gamepix.py")
    return sdk.split_presets(root, _SELF, preset, out, others)


def bare_presets(presets_text: str) -> list[str]:
    """Warnings for every `GamePix*` preset that looks like the plugin's own.

    The plugin's block has an empty exclude_filter and no custom_template keys.
    A build through it ships everything the project holds, and ship.py cannot
    write the machine's export template into a key that is not there -- so the
    export quietly uses the stock template. Read-only.
    """
    warnings = []
    starts = list(re.finditer(r"^\[preset\.(\d+)\]$", presets_text, flags=re.M))
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(presets_text)
        body = presets_text[match.start():end]
        name = re.search(r'^name="([^"]*)"$', body, flags=re.M)
        if not name or not name.group(1).startswith(PRESET_PREFIX):
            continue
        exclude = re.search(r'^exclude_filter="([^"]*)"$', body, flags=re.M)
        if not exclude or not exclude.group(1).strip():
            warnings.append(f'preset "{name.group(1)}" has an empty exclude_filter -- this is the '
                            "block the GamePix plugin writes by itself, and a build through it "
                            "ships docs, tools and every dev addon. Delete it and run: "
                            f"ship.py {COMMAND}")
        if not re.search(r"^custom_template/release=", body, flags=re.M):
            warnings.append(f'preset "{name.group(1)}" has no custom_template/release key, so the '
                            "machine's export template cannot be written into it and the export "
                            "uses the stock one")
    return warnings


def review(root: Path, presets_text: str) -> list[str]:
    return bare_presets(presets_text)


import sys as _sys  # noqa: E402
_SELF = _sys.modules[__name__]
