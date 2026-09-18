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
what a variant's `strip` is for, and `split_presets` below writes the two
presets so that each carries exactly its own half.

Everything that writes is called from `ship.py playgama` and from a build of a
variant that declares the addon. Nothing here runs on `doctor` or `review`.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

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
# What the plain web build must not carry. `template/*` alone is not it: the
# plugin and the export postprocessor are editor code and ship as dead weight,
# and bridge.gd is the autoload the other variant strips.
EXCLUDE = "addons/playgama_bridge/*"

PRESET = "Web Playgama"
OUT = "build/playgama/web/index.html"
# What `ship.py playgama` tells the user to put in godot-ship.yaml when no
# variant declares the addon yet -- block form only, lib/yamlish reads no flow map.
STARTER = [
    "variants:",
    "  playgama:",
    "    platform: web",
    f'    preset: "{PRESET}"',
    f"    out: {OUT}",
    "    archive: build/playgama/<game>-web.zip   # flat, index.html at the root",
    "    addon: playgama_bridge",
    "  web:",
    "    strip:",
    f'      autoloads: ["{AUTOLOAD}"]',
    f'      plugins: ["{PLUGIN}"]',
    "targets: [..., web, playgama]",
]


def installed(root: Path) -> bool:
    return (root / ADDON / "plugin.cfg").is_file()


def version(root: Path) -> str:
    text = (root / ADDON / "plugin.cfg").read_text(encoding="utf-8", errors="replace")
    found = re.search(r'^version="([^"]*)"', text, flags=re.M)
    return f"v{found.group(1)}" if found else "(version unknown)"


def install(root: Path, source: str | None) -> None:
    """Copy the addon in from a local checkout or zip, else download the latest release."""
    target = root / ADDON
    if installed(root):
        print(f"  {ADDON} already there ({version(root)})")
        return
    keep_out = shutil.ignore_patterns(".git", "__pycache__")
    if source:
        src = Path(source)
        if src.is_file() and src.suffix == ".zip":
            _unzip(src.read_bytes(), target)
        elif (src / ADDON).is_dir():
            shutil.copytree(src / ADDON, target, ignore=keep_out, dirs_exist_ok=True)
        elif (src / "plugin.cfg").is_file():
            shutil.copytree(src, target, ignore=keep_out, dirs_exist_ok=True)
        else:
            raise SystemExit(f"playgama_bridge_src is {source}: not a release zip, not a "
                             f"checkout holding {ADDON}, and not the addon folder itself")
        print(f"  {ADDON} <- {source}")
        return
    print(f"  downloading the latest release of {UPSTREAM}")
    tag, url = _latest_asset()
    _unzip(_fetch(url), target)
    print(f"  {ADDON} <- {tag}")


def _fetch(url: str) -> bytes:
    # GitHub's API answers a request with no User-Agent with 403, not with JSON.
    request = urllib.request.Request(url, headers={"User-Agent": "godot-ship",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _latest_asset() -> tuple[str, str]:
    try:
        release = json.loads(_fetch(LATEST))
    except OSError as why:
        raise SystemExit(f"could not read {LATEST}: {why}\n  Download the release zip "
                         f"by hand from {UPSTREAM}/releases and name it in "
                         ".godot-ship.local.yaml as playgama_bridge_src.")
    for asset in release.get("assets") or []:
        if ASSET.match(asset.get("name", "")):
            return release.get("tag_name", "?"), asset["browser_download_url"]
    raise SystemExit(f"release {release.get('tag_name')} of {UPSTREAM} has no playgama_bridge*.zip "
                     "asset -- the download name changed; fetch it by hand and set "
                     "playgama_bridge_src")


def _unzip(data: bytes, target: Path) -> None:
    """The zip has held `addons/playgama_bridge/...` in some releases and the
    addon folder at its root in others; the plugin.cfg says where the addon is."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory() as tmp:
        archive.extractall(tmp)
        found = sorted(Path(tmp).rglob("plugin.cfg"), key=lambda p: len(p.parts))
        if not found:
            raise SystemExit("the playgama_bridge zip holds no plugin.cfg -- not the addon")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(found[0].parent, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))


def enable(root: Path) -> None:
    """Register the plugin and the autoload in project.godot.

    The autoload goes FIRST in [autoload]: everything that talks to the SDK sits
    after it, and Godot instantiates autoloads in file order. The plugin would
    add it on its own the next time the editor loads -- at the END of the list,
    after the scripts that need it.
    """
    manifest = root / "project.godot"
    text = manifest.read_text(encoding="utf-8")
    original = text
    if PLUGIN not in text:
        section = re.search(r'(?m)^enabled=PackedStringArray\((.*)\)$', text)
        if section:
            inner = section.group(1).strip()
            joined = f'"{PLUGIN}"' if not inner else f'{inner}, "{PLUGIN}"'
            text = text[:section.start()] + f"enabled=PackedStringArray({joined})" \
                + text[section.end():]
        elif re.search(r"(?m)^\[editor_plugins\]$", text):
            text = re.sub(r"(?m)^\[editor_plugins\]$",
                          f'[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")',
                          text, count=1)
        else:
            text = text.rstrip("\n") + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")\n'
    if not re.search(rf"(?m)^{AUTOLOAD}=", text):
        line = f'{AUTOLOAD}="{AUTOLOAD_PATH}"'
        if re.search(r"(?m)^\[autoload\]$", text):
            text = re.sub(r"(?m)^\[autoload\]$", f"[autoload]\n\n{line}", text, count=1)
        else:
            text = text.rstrip("\n") + f"\n\n[autoload]\n\n{line}\n"
    if text != original:
        manifest.write_text(text, encoding="utf-8")


def split_presets(root: Path, preset: str, out: str) -> list[str]:
    """One Web preset becomes two: the store's and the plain one.

    Returns what changed, one line each. The plain `Web` preset loses the Bridge
    shell if it had it and gains `addons/playgama_bridge/*` in its exclude
    filter; the new block is a copy of it named `preset`, exporting to `out`,
    with the shell put back and the addon NOT excluded. Nothing else in either
    block is touched: the project's own settings -- the head include, the
    icon, the texture formats -- belong to the project.
    """
    presets = root / "export_presets.cfg"
    if not presets.is_file():
        raise SystemExit("no export_presets.cfg -- run `ship.py build web` once so the Web "
                         "preset exists, then `ship.py playgama`")
    text = presets.read_text(encoding="utf-8")
    starts = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
    blocks = [(m.start(), starts[i + 1].start() if i + 1 < len(starts) else len(text))
              for i, m in enumerate(starts)]
    bodies = [text[s:e] for s, e in blocks]
    names = [re.search(r'^name="([^"]*)"$', b, flags=re.M) for b in bodies]
    names = [n.group(1) if n else "" for n in names]
    done: list[str] = []
    if preset in names:
        done.append(f'preset "{preset}" already in export_presets.cfg, left alone')
        return done

    plain = next((i for i, b in enumerate(bodies)
                  if re.search(r'^platform="Web"$', b, flags=re.M)), None)
    if plain is None:
        raise SystemExit("no Web preset to copy -- run `ship.py build web` once, then "
                         "`ship.py playgama`")
    body = bodies[plain]

    # The plain web build: the shell off, the addon out.
    plain_body = re.sub(r'^html/custom_html_shell=".*"$', 'html/custom_html_shell=""',
                        body, flags=re.M)
    exclude = re.search(r'^exclude_filter="([^"]*)"$', plain_body, flags=re.M)
    current = exclude.group(1) if exclude else ""
    kept = [p.strip() for p in current.split(",") if p.strip()
            and not p.strip().startswith(("addons/playgama_bridge", "res://addons/playgama_bridge"))]
    joined = ",".join(kept + [EXCLUDE])
    if exclude:
        plain_body = plain_body[:exclude.start()] + f'exclude_filter="{joined}"' + plain_body[exclude.end():]
    if plain_body != body:
        done.append(f'"{names[plain]}": shell cleared, {EXCLUDE} excluded -- the plain web build')

    # The store's: same block, its own name, output and shell, the addon in.
    index = max(int(m.group(1)) for m in starts) + 1
    old_index = re.match(r"^\[preset\.(\d+)\]", body).group(1)
    # Both headers: `[preset.N]` and its `[preset.N.options]`. Renumbering only
    # the first leaves a second `[preset.2.options]` in the file, and Godot then
    # reads the store's options into the plain preset.
    store = re.sub(rf"^\[preset\.{old_index}((?:\.options)?)\]$",
                   lambda m: f"[preset.{index}{m.group(1)}]", body, flags=re.M)
    store = re.sub(r'^name=".*"$', f'name="{preset}"', store, count=1, flags=re.M)
    store = re.sub(r'^export_path=".*"$', f'export_path="{out}"', store, count=1, flags=re.M)
    store = re.sub(r'^html/custom_html_shell=".*"$', f'html/custom_html_shell="{SHELL}"',
                   store, flags=re.M)
    if f'html/custom_html_shell="{SHELL}"' not in store:
        store = store.rstrip("\n") + f'\nhtml/custom_html_shell="{SHELL}"\n'
    # Only a whole-addon exclusion comes out of the store's preset. A project
    # that already holds the editor half out -- plugin.gd, postprocessor.gd,
    # template/* -- keeps doing so: that is editor code and the shell source,
    # not the SDK, and it was excluded for a reason.
    exclude = re.search(r'^exclude_filter="([^"]*)"$', store, flags=re.M)
    if exclude:
        whole = {EXCLUDE, "res://" + EXCLUDE, EXCLUDE.rstrip("/*"), "res://" + EXCLUDE.rstrip("/*")}
        kept = [p.strip() for p in exclude.group(1).split(",") if p.strip() and p.strip() not in whole]
        store = store[:exclude.start()] + f'exclude_filter="{",".join(kept)}"' + store[exclude.end():]
    if not store.endswith("\n"):
        store += "\n"
    store = "\n" + store.lstrip("\n")

    s, e = blocks[plain]
    text = text[:s] + plain_body + text[e:]
    text = text.rstrip("\n") + "\n" + store
    presets.write_text(text, encoding="utf-8")
    done.append(f'"{preset}" added -> {out}, Bridge shell on, the addon in')
    return done
