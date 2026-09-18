"""What every store-SDK addon has in common: getting it in, registering it, and
giving it a web preset of its own.

A store's SDK is three things that only work together -- the addon folder, the
autoload its plugin registers, and an HTML shell named by a web preset -- and a
fourth that is easy to forget: it must be ABSENT from every other store's build.
Stores refuse a build that carries a rival's SDK, and an autoload whose files a
preset excludes prints ERROR lines on the player's first frame.

A provider is a module (`lib/playgama.py`, `lib/gamepix.py`) holding the names
its upstream plugin hardcodes:

    KEY            what a variant writes under `addon:`; `<KEY>_src` in the local config
    COMMAND        the ship.py subcommand that sets it up
    VARIANT        the variant name the printed yaml proposes
    ADDON          the addon folder, relative to the project
    PLUGIN         its plugin.cfg, as project.godot names it
    AUTOLOAD       the singleton its plugin registers, and AUTOLOAD_PATH, its value
    SHELL          html/custom_html_shell for its preset
    EXCLUDE        the pattern that holds the whole addon out of another preset
    PRESET, OUT    the preset it gets and where that exports
    download()     -> (label, zip bytes) from upstream

The functions here take the provider as `p`. Everything that writes is reached
only from the provider's own command and from a build of a variant naming it.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

KEEP_OUT = shutil.ignore_patterns(".git", "__pycache__")


def installed(root: Path, p) -> bool:
    return (root / p.ADDON / "plugin.cfg").is_file()


def version(root: Path, p) -> str:
    text = (root / p.ADDON / "plugin.cfg").read_text(encoding="utf-8", errors="replace")
    found = re.search(r'^version="([^"]*)"', text, flags=re.M)
    return f"v{found.group(1)}" if found else "(version unknown)"


def fetch(url: str) -> bytes:
    # GitHub's API answers a request with no User-Agent with 403, not with JSON,
    # and a CDN in front of a store's download does the same to urllib's default.
    request = urllib.request.Request(url, headers={"User-Agent": "godot-ship",
                                                   "Accept": "application/vnd.github+json, */*"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def unzip(data: bytes, target: Path) -> None:
    """Copy the folder holding plugin.cfg to `target`, whatever the zip calls it.

    Release zips have held `addons/<name>/...`, the addon folder at the root, and
    a folder under the upstream's own name; the plugin.cfg says where the addon
    is. The TARGET name is never taken from the zip -- plugins hardcode their
    own res:// paths, and a folder under any other name loads and then cannot
    find its shell.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory() as tmp:
        archive.extractall(tmp)
        found = sorted(Path(tmp).rglob("plugin.cfg"), key=lambda path: len(path.parts))
        if not found:
            raise SystemExit("the zip holds no plugin.cfg -- it is not a Godot addon")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(found[0].parent, target, dirs_exist_ok=True, ignore=KEEP_OUT)


def install(root: Path, p, source: str | None) -> None:
    """From `<KEY>_src` -- a release zip, the addon folder, or a checkout holding
    it -- else from upstream."""
    target = root / p.ADDON
    if installed(root, p):
        print(f"  {p.ADDON} already there ({version(root, p)})")
        return
    if source:
        src = Path(source)
        if src.is_file() and src.suffix == ".zip":
            unzip(src.read_bytes(), target)
        elif (src / p.ADDON).is_dir():
            shutil.copytree(src / p.ADDON, target, ignore=KEEP_OUT, dirs_exist_ok=True)
        elif (src / "plugin.cfg").is_file():
            shutil.copytree(src, target, ignore=KEEP_OUT, dirs_exist_ok=True)
        else:
            raise SystemExit(f"{p.KEY}_src is {source}: not a release zip, not a checkout "
                             f"holding {p.ADDON}, and not the addon folder itself")
        print(f"  {p.ADDON} <- {source}")
        return
    label, data = p.download()
    unzip(data, target)
    print(f"  {p.ADDON} <- {label}")


def enable(root: Path, p) -> None:
    """Register the plugin and the autoload in project.godot.

    The autoload goes FIRST in [autoload]: everything that talks to the SDK sits
    after it, and Godot instantiates autoloads in file order. The plugin would
    add it on its own the next time the editor loads -- at the END of the list,
    after the scripts that need it in their `_ready`.
    """
    manifest = root / "project.godot"
    text = manifest.read_text(encoding="utf-8")
    original = text
    if p.PLUGIN not in text:
        section = re.search(r'(?m)^enabled=PackedStringArray\((.*)\)$', text)
        if section:
            inner = section.group(1).strip()
            joined = f'"{p.PLUGIN}"' if not inner else f'{inner}, "{p.PLUGIN}"'
            text = text[:section.start()] + f"enabled=PackedStringArray({joined})" \
                + text[section.end():]
        elif re.search(r"(?m)^\[editor_plugins\]$", text):
            text = re.sub(r"(?m)^\[editor_plugins\]$",
                          lambda m: f'[editor_plugins]\n\nenabled=PackedStringArray("{p.PLUGIN}")',
                          text, count=1)
        else:
            text = text.rstrip("\n") \
                + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{p.PLUGIN}")\n'
    if not re.search(rf"(?m)^{re.escape(p.AUTOLOAD)}=", text):
        line = f'{p.AUTOLOAD}="{p.AUTOLOAD_PATH}"'
        if re.search(r"(?m)^\[autoload\]$", text):
            # The header and the blank line(s) under it, replaced together: inserting
            # after the header alone leaves a gap between this entry and the next.
            text = re.sub(r"(?m)^\[autoload\]\n+", lambda m: f"[autoload]\n\n{line}\n", text, count=1)
        else:
            text = text.rstrip("\n") + f"\n\n[autoload]\n\n{line}\n"
    if text != original:
        manifest.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------- presets

def _folder(p) -> str:
    return p.EXCLUDE.rstrip("*").rstrip("/")


def _patterns(body: str) -> tuple[re.Match | None, list[str]]:
    found = re.search(r'^exclude_filter="([^"]*)"$', body, flags=re.M)
    return found, [part.strip() for part in (found.group(1) if found else "").split(",")
                   if part.strip()]


def _with_patterns(body: str, found: re.Match | None, patterns: list[str]) -> str:
    line = f'exclude_filter="{",".join(patterns)}"'
    if found:
        return body[:found.start()] + line + body[found.end():]
    # A preset the editor never saved has no exclude_filter line at all; it goes
    # in the preset's own section, which ends at its `.options` header.
    options = re.search(r"^\[preset\.\d+\.options\]$", body, flags=re.M)
    at = options.start() if options else len(body)
    return body[:at].rstrip("\n") + "\n" + line + "\n\n" + body[at:]


def hold_out(body: str, p) -> str:
    """The whole addon excluded from this preset. Narrower patterns under the same
    folder go: they were holding out the editor half of an SDK this preset now
    holds out entirely, and left in they read as if the rest still ships."""
    found, patterns = _patterns(body)
    folder = _folder(p)
    kept = [part for part in patterns
            if not part.removeprefix("res://").startswith(folder)]
    return _with_patterns(body, found, kept + [p.EXCLUDE])


def let_in(body: str, p) -> str:
    """Only a whole-addon exclusion comes out. A project that already holds the
    editor half out -- plugin.gd, an export postprocessor, the shell's source
    folder -- keeps doing so: that is editor code, not the SDK, and it was
    excluded for a reason."""
    found, patterns = _patterns(body)
    folder = _folder(p)
    whole = {p.EXCLUDE, "res://" + p.EXCLUDE, folder, "res://" + folder}
    kept = [part for part in patterns if part not in whole]
    return _with_patterns(body, found, kept) if kept != patterns else body


def split_presets(root: Path, p, preset: str, out: str, others=()) -> list[str]:
    """Give the provider a web preset of its own and hold it out of the rest.

    Returns what changed, one line each. The new block is a copy of the plain
    `Web` preset named `preset`, exporting to `out`, with the provider's shell,
    its addon let in and every OTHER installed store SDK held out. Every other
    web preset gains the provider's whole-addon exclusion, and loses the
    provider's shell if it had it. Nothing else in any block is touched: the
    project's own settings -- the head include, the icon, the texture formats
    -- belong to the project.
    """
    presets = root / "export_presets.cfg"
    if not presets.is_file():
        raise SystemExit("no export_presets.cfg -- run `ship.py build web` once so the Web "
                         f"preset exists, then `ship.py {p.COMMAND}`")
    text = presets.read_text(encoding="utf-8")
    starts = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
    if not starts:
        raise SystemExit("export_presets.cfg holds no preset -- run `ship.py build web` once")
    head = text[:starts[0].start()]
    bodies = [text[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(text))]
              for i, m in enumerate(starts)]
    names = [(re.search(r'^name="([^"]*)"$', body, flags=re.M) or [None, ""])[1]
             for body in bodies]
    web = [i for i, body in enumerate(bodies) if re.search(r'^platform="Web"$', body, flags=re.M)]
    if not web:
        raise SystemExit("no Web preset to copy -- run `ship.py build web` once, then "
                         f"`ship.py {p.COMMAND}`")
    # The plain one: called "Web", else the first that is nobody's store preset.
    stores = {preset, p.PRESET, *(other.PRESET for other in others)}
    plain = next((i for i in web if names[i] == "Web"), None)
    if plain is None:
        plain = next((i for i in web if names[i] not in stores), web[0])
    source = bodies[plain]      # the copy is taken before the provider is held out of it

    done: list[str] = []
    own = names.index(preset) if preset in names else None
    if own is not None:
        done.append(f'preset "{preset}" already in export_presets.cfg, left alone')

    for i in web:
        if i == own:
            continue
        body = hold_out(bodies[i], p)
        cleared = re.sub(rf'^html/custom_html_shell="{re.escape(p.SHELL)}"$',
                         'html/custom_html_shell=""', body, flags=re.M)
        what = [f"{p.EXCLUDE} excluded"] if body != bodies[i] else []
        if cleared != body:
            what.insert(0, "shell cleared")
        if what:
            done.append(f'"{names[i]}": {", ".join(what)}')
        bodies[i] = cleared

    if own is None:
        index = max(int(m.group(1)) for m in starts) + 1
        old_index = re.match(r"^\[preset\.(\d+)\]", source).group(1)
        # Both headers: `[preset.N]` and its `[preset.N.options]`. Renumbering only
        # the first leaves a second `[preset.2.options]` in the file, and Godot then
        # reads the store's options into the plain preset.
        store = re.sub(rf"^\[preset\.{old_index}((?:\.options)?)\]$",
                       lambda m: f"[preset.{index}{m.group(1)}]", source, flags=re.M)
        store = re.sub(r'^name=".*"$', lambda m: f'name="{preset}"', store, count=1, flags=re.M)
        store = re.sub(r'^export_path=".*"$', lambda m: f'export_path="{out}"', store,
                       count=1, flags=re.M)
        shell = f'html/custom_html_shell="{p.SHELL}"'
        store = re.sub(r'^html/custom_html_shell=".*"$', lambda m: shell, store, flags=re.M)
        if shell not in store:
            store = store.rstrip("\n") + f"\n{shell}\n"
        store = let_in(store, p)
        held = []
        for other in others:
            if installed(root, other):
                store = hold_out(store, other)
                held.append(other.EXCLUDE)
        bodies.append("\n" + store.strip("\n") + "\n")
        bodies[-2] = bodies[-2].rstrip("\n") + "\n"
        done.append(f'"{preset}" added -> {out}, its shell on, the addon in'
                    + (f", {', '.join(held)} out" if held else ""))

    rebuilt = head + "".join(bodies)
    if rebuilt != text:
        presets.write_text(rebuilt, encoding="utf-8")
    return done


# ------------------------------------------------------------------ the yaml block

def starter(p, others=()) -> list[str]:
    """What the provider's command tells the user to put in godot-ship.yaml.

    Block form only -- lib/yamlish reads no flow map. Every web variant strips
    the SDKs it does not name: the plugin registers its autoload by itself, so
    a preset's exclude_filter alone leaves the autoload pointing at nothing.
    """
    def strip(indent: str, providers) -> list[str]:
        autoloads = ", ".join(f'"{x.AUTOLOAD}"' for x in providers)
        plugins = ", ".join(f'"{x.PLUGIN}"' for x in providers)
        return [f"{indent}strip:", f"{indent}  autoloads: [{autoloads}]",
                f"{indent}  plugins: [{plugins}]"]

    lines = [
        "# A variant's `strip` REPLACES the top-level one: keep what these variants",
        "# already strip and add the names below to it.",
        "variants:",
        f"  {p.VARIANT}:",
        "    platform: web",
        f'    preset: "{p.PRESET}"',
        f"    out: {p.OUT}",
        f"    archive: build/{p.VARIANT}/<game>-web.zip   # flat, index.html at the root",
        f"    addon: {p.KEY}",
    ]
    if others:
        lines += strip("    ", others)
    for other in others:
        lines += [f"  {other.VARIANT}:"] + strip("    ", [p] + [o for o in others if o is not other])
    lines += ["  web:"] + strip("    ", [p, *others])
    lines.append(f"targets: [..., web, {p.VARIANT}]")
    return lines


# ----------------------------------------------------------------------- review

def foreign(root: Path, own, providers, exclude_filter: str, strip: dict) -> list[str]:
    """Each web variant carries exactly the SDK it names, and no other.

    `own` is the provider the variant declares under `addon:`, or None for a
    plain web build. For every OTHER provider installed in the tree, both halves
    must be out: its autoload in this variant's `strip.autoloads`, and its
    script excluded by this variant's preset. Stores refuse a rival's SDK --
    GamePix: "games must either use the GamePix SDK or be entirely SDK-free" --
    and a rival's shell-less SDK still runs its autoload, calls home, and shows
    up in the store's network log. Read-only.
    """
    from .review import excluded
    warnings = []
    dropped = set(strip.get("autoloads") or [])
    unplugged = set(strip.get("plugins") or [])
    for other in providers:
        if other is own or not installed(root, other):
            continue
        script = other.AUTOLOAD_PATH.lstrip("*")
        if other.AUTOLOAD not in dropped:
            warnings.append(f"still registers {other.AUTOLOAD}, another store's SDK -- add it "
                            "to this variant's strip.autoloads")
        if other.PLUGIN not in unplugged:
            warnings.append(f"keeps the plugin {other.PLUGIN} enabled -- it re-registers "
                            f"{other.AUTOLOAD} during the export; add it to strip.plugins")
        if not excluded(script, exclude_filter):
            warnings.append(f"its preset does not exclude {other.EXCLUDE} -- another store's "
                            "SDK ships in this build")
    return warnings
