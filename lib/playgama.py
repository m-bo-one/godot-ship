"""Playgama Bridge: the SDK a Playgama web build cannot run without.

Playgama's store loads a Godot web export through the addon's own HTML shell
(`html/custom_html_shell`), and the addon's export plugin drops `playgama-bridge.js`
and its config beside `index.html`. Without any one of those the build exports
cleanly and loads on the store with no SDK -- no saves, no ads, no error line
anywhere. So the whole set is put in together, and a variant that names
`addon: playgama_bridge` gets it installed before its first export.

The same addon must NOT reach the plain web build for another store: its
autoload `Bridge` is registered by the plugin itself (`add_autoload_singleton`
in plugin.gd), so holding the files out of that preset is not enough -- the
autoload survives in project.godot and the first frame is ERROR lines. That is
what a variant's `strip` is for, and `split_presets` writes the presets so that
each carries exactly its own half.

Bridge is also how several OTHER stores are reached -- GameDistribution,
CrazyGames, Yandex and more are platforms inside the same js, picked at run
time by hostname -- so one Playgama build serves those; only a store with a
Godot plugin of its own (see lib/gamepix.py) needs a second provider.

The mechanics are lib/sdk.py; this file is the names upstream hardcodes. What
writes is called from `ship.py playgama` and from a build of a variant that
declares the addon. Nothing here runs on `doctor` or `review`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import sdk

KEY = "playgama_bridge"
COMMAND = "playgama"
VARIANT = "playgama"

# Playgama/bridge-godot-4 is the Godot 4 line; `bridge-godot` without the suffix
# is the Godot 3 addon, and it installs without complaint into a 4.x project.
UPSTREAM = "https://github.com/Playgama/bridge-godot-4"
LATEST = "https://api.github.com/repos/Playgama/bridge-godot-4/releases/latest"
ASSET = re.compile(r"^playgama_bridge.*\.zip$")

ADDON = "addons/playgama_bridge"
PLUGIN = "res://addons/playgama_bridge/plugin.cfg"
AUTOLOAD = "Bridge"
AUTOLOAD_PATH = "*res://addons/playgama_bridge/bridge.gd"
SHELL = "res://addons/playgama_bridge/template/index.html"
# What another store's web build must not carry. `template/*` alone is not it:
# the plugin and the export postprocessor are editor code and ship as dead
# weight, and bridge.gd is the autoload the other variant strips.
EXCLUDE = "addons/playgama_bridge/*"

PRESET = "Web Playgama"
OUT = "build/playgama/web/index.html"


def download() -> tuple[str, bytes]:
    print(f"  downloading the latest release of {UPSTREAM}")
    try:
        release = json.loads(sdk.fetch(LATEST))
    except OSError as why:
        raise SystemExit(f"could not read {LATEST}: {why}\n  Download the release zip "
                         f"by hand from {UPSTREAM}/releases and name it in "
                         f".godot-ship.local.yaml as {KEY}_src.")
    for asset in release.get("assets") or []:
        if ASSET.match(asset.get("name", "")):
            return release.get("tag_name", "?"), sdk.fetch(asset["browser_download_url"])
    raise SystemExit(f"release {release.get('tag_name')} of {UPSTREAM} has no "
                     "playgama_bridge*.zip asset -- the download name changed; fetch it by "
                     f"hand and set {KEY}_src")


def installed(root: Path) -> bool:
    return sdk.installed(root, _SELF)


def version(root: Path) -> str:
    return sdk.version(root, _SELF)


def install(root: Path, source: str | None) -> None:
    sdk.install(root, _SELF, source)


def enable(root: Path) -> None:
    sdk.enable(root, _SELF)


def split_presets(root: Path, preset: str, out: str, others=()) -> list[str]:
    return sdk.split_presets(root, _SELF, preset, out, others)


def review(root: Path, presets_text: str) -> list[str]:
    """Nothing of Bridge's own to warn about beyond what every SDK is held to."""
    return []


import sys as _sys  # noqa: E402
_SELF = _sys.modules[__name__]
