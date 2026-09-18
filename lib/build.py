#!/usr/bin/env python3
"""Export a Godot project for Windows, macOS and Web.

    python build.py                     # every platform whose templates exist
    python build.py windows web         # only the named ones
    python build.py --debug windows     # debug build instead of release
    python build.py --list              # show what is installed, change nothing
    python build.py --project <project root> windows
    python build.py web --preset "Web Playgama" --out build/playgama/web/index.html
                                        # one named preset of that platform

Nothing is hardcoded. The project root is found by walking up for
`project.godot`; the engine comes from $GODOT / $GODOT_EXE / $GODOT_BIN, then
PATH, then the usual install dirs; the export-template folder is matched to the
engine's own version string; a missing export preset is appended to
`export_presets.cfg` under the project's own name.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# A Windows console defaults to cp1252 and dies on any non-Latin output.
for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "") and _stream.encoding.lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# CLI name -> (platform= value in export_presets.cfg, preset name,
#              export path template, export-template filename prefix)
PLATFORMS = {
    "windows": ("Windows Desktop", "Windows Desktop", "build/{name}.exe", "windows"),
    "macos": ("macOS", "macOS", "build/{name}.zip", "macos"),
    "web": ("Web", "Web", "build/web/index.html", "web"),
}

# Only the options without which the export fails or produces the wrong
# artifact. Godot fills in every other option with its own default.
PRESET_OPTIONS = {
    "windows": [
        # Ship the .pck beside the .exe: embedding it bloats Steam delta
        # patches and makes some antivirus heuristics flag the executable.
        ("binary_format/embed_pck", "false"),
        ("binary_format/architecture", '"x86_64"'),
        ("texture_format/s3tc_bptc", "true"),
        ("texture_format/etc2_astc", "false"),
    ],
    # bundle_identifier must be non-empty or the export aborts with a bare
    # "configuration errors" line; it is filled in from the project name below.
    "macos": [
        ("binary_format/architecture", '"universal"'),
        ("application/bundle_identifier", '"{bundle_id}"'),
        ("application/app_category", '"Games"'),
        ("application/short_version", '"1.0"'),
        ("application/version", '"1.0"'),
        ("application/min_macos_version_x86_64", '"10.12"'),
        ("application/min_macos_version_arm64", '"11.00"'),
        ("display/high_res", "true"),
        ("texture_format/s3tc_bptc", "true"),
        ("texture_format/etc2_astc", "true"),
        ("codesign/codesign", "0"),
        ("export/distribution_type", "0"),
    ],
    # for_mobile=true drags in the ETC2 requirement and fails the export;
    # desktop-only VRAM compression is the working Web configuration.
    # thread_support=false keeps the build runnable on hosts that do not send
    # COOP/COEP headers, and on mobile Safari; threads need SharedArrayBuffer.
    "web": [
        ("variant/extensions_support", "false"),
        ("variant/thread_support", "false"),
        ("vram_texture_compression/for_desktop", "true"),
        ("vram_texture_compression/for_mobile", "false"),
        ("html/export_icon", "true"),
        ("html/canvas_resize_policy", "2"),
        ("html/focus_canvas_on_start", "true"),
        ("progressive_web_app/enabled", "false"),
    ],
}

# macOS universal/arm64 refuses to export without this project setting.
# project.godot drops the section prefix: rendering/textures/vram_compression/
# import_etc2_astc is stored under [rendering] as textures/vram_compression/
# import_etc2_astc. The fully-qualified name parses fine and is ignored.
ETC2_SECTION = "rendering"
ETC2_KEY = "textures/vram_compression/import_etc2_astc"
NEEDS_ETC2 = {"macos"}

GODOT_ENV_VARS = ("GODOT", "GODOT_EXE", "GODOT_BIN")
GODOT_GUESSES = ("godot", "godot4", "Godot")


def find_project(start: Path) -> Path:
    for folder in [start, *start.parents]:
        if (folder / "project.godot").is_file():
            return folder
    sys.exit(f"no project.godot in {start} or any parent directory")


def find_godot() -> str:
    for var in GODOT_ENV_VARS:
        exe = os.environ.get(var)
        if exe and Path(exe).is_file():
            return exe
    for guess in GODOT_GUESSES:
        found = shutil.which(guess)
        if found:
            return found
    # Last resort: anything in $GODOT_SEARCH, then the directory this project
    # sits in. Naming an install directory here would put one machine's layout
    # into shared code -- ship.py sets $GODOT before calling this anyway.
    folders = [Path(entry) for entry in (os.environ.get("GODOT_SEARCH") or "").split(os.pathsep)
               if entry.strip()]
    folders += [Path.cwd().parent, Path.home()]
    for root in folders:
        if root.is_dir():
            hits = [h for h in sorted(root.glob("godot*"), key=lambda p: p.stat().st_mtime,
                                      reverse=True)
                    if h.is_file() and os.access(h, os.X_OK)]
            if hits:
                return str(hits[0])
    sys.exit('Godot not found. Point $GODOT at it, e.g.\n'
             '  $env:GODOT = "<full path to the godot binary>"')


def engine_version(godot: str) -> str:
    """`4.7.stable.official.<hash>` -> `4.7.stable`, the template folder name."""
    out = subprocess.run([godot, "--version"], capture_output=True, text=True,
                         timeout=60).stdout.strip().splitlines()[-1]
    match = re.match(r"(\d+\.\d+(?:\.\d+)?)\.(\w+)", out)
    return f"{match.group(1)}.{match.group(2)}" if match else out


def templates_dir(version: str) -> Path | None:
    roots = []
    if os.name == "nt":
        roots.append(Path(os.environ.get("APPDATA", "")) / "Godot" / "export_templates")
    roots += [
        Path.home() / ".local/share/godot/export_templates",
        Path.home() / "Library/Application Support/Godot/export_templates",
    ]
    for root in roots:
        folder = root / version
        if folder.is_dir():
            return folder
    return None


def have_template(folder: Path | None, prefix: str, debug: bool) -> bool:
    if folder is None:
        return False
    if prefix == "macos":
        return (folder / "macos.zip").is_file()
    kind = "debug" if debug else "release"
    return any(p.name.startswith(f"{prefix}_{kind}") for p in folder.iterdir())


def _preset_blocks(cfg: Path) -> list[str]:
    """Every [preset.N] block with its [preset.N.options], as text."""
    if not cfg.is_file():
        return []
    text = cfg.read_text(encoding="utf-8")
    starts = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
    return [text[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(text))]
            for i, m in enumerate(starts)]


def read_presets(cfg: Path) -> dict[str, str]:
    """platform= -> name= for every preset in the file.

    The FIRST preset of a platform wins. With two Web presets -- one per store --
    this is only the fallback for a bare platform target; a named variant is
    looked up by name with `preset_body`, never by platform.
    """
    found = {}
    for block in _preset_blocks(cfg):
        name = re.search(r'^name="([^"]*)"', block, flags=re.M)
        platform = re.search(r'^platform="([^"]*)"', block, flags=re.M)
        if name and platform:
            found.setdefault(platform.group(1), name.group(1))
    return found


def preset_body(cfg: Path, name: str | None) -> str | None:
    """The block of the preset called `name`, or None when the file has none."""
    if not name:
        return None
    for block in _preset_blocks(cfg):
        if re.search(rf'^name="{re.escape(name)}"$', block, flags=re.M):
            return block
    return None


def next_preset_index(cfg: Path) -> int:
    if not cfg.is_file():
        return 0
    seen = [int(n) for n in re.findall(r"^\[preset\.(\d+)\]$",
                                       cfg.read_text(encoding="utf-8"), flags=re.M)]
    return max(seen) + 1 if seen else 0


def artifact_name(root: Path) -> str:
    match = re.search(r'^config/name="([^"]*)"',
                      (root / "project.godot").read_text(encoding="utf-8"), flags=re.M)
    raw = match.group(1) if match else root.name
    # Keep the filename ASCII: non-Latin names break web hosting and signing.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return slug or root.name


def project_version(root: Path) -> str:
    match = re.search(r'^config/version="([^"]*)"',
                      (root / "project.godot").read_text(encoding="utf-8"), flags=re.M)
    return match.group(1) if match and match.group(1).strip() else "0.0.0"


def set_project_field(root: Path, key: str, value: str) -> None:
    """Write one [application] key in project.godot, adding it when absent."""
    manifest = root / "project.godot"
    text = manifest.read_text(encoding="utf-8")
    line = f'{key}="{value}"'
    if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", line, text, count=1, flags=re.M)
    else:
        text = re.sub(r"^\[application\]$", f"[application]\n\n{line}", text, count=1, flags=re.M)
    manifest.write_text(text, encoding="utf-8")


def bumped(version: str, part: str) -> str:
    numbers = [int(p) if p.isdigit() else 0 for p in version.split(".")] + [0, 0, 0]
    major, minor, patch = numbers[:3]
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def stamp(root: Path, cfg: Path, version: str, debug: bool) -> None:
    """Mirror config/version into the Windows preset and date a release build.

    config/version is the one place the number is written down: the settings
    screen reads it at runtime and the itch upload takes its --userversion from
    it. Windows wants four components, and the .exe properties are the only
    place a player can read a version without launching the game.
    """
    four = ".".join((version.split(".") + ["0", "0", "0"])[:4])
    text = cfg.read_text(encoding="utf-8")
    for key in ("application/file_version", "application/product_version"):
        text = re.sub(rf'^{re.escape(key)}=.*$', f'{key}="{four}"', text, flags=re.M)
    cfg.write_text(text, encoding="utf-8")
    # A debug export is not a release and must not move the date the UI shows.
    if not debug:
        set_project_field(root, "config/build_date", datetime.date.today().isoformat())


def ensure_etc2_astc(root: Path) -> bool:
    """Turn on ETC2 ASTC import; returns True when project.godot changed.

    Without it Godot aborts a macOS universal/arm64 export outright and fails a
    Web export with a bare "configuration errors" line and no explanation.
    """
    manifest = root / "project.godot"
    text = manifest.read_text(encoding="utf-8")
    # Drop any earlier attempt that wrote the fully-qualified name.
    text = re.sub(rf"^{ETC2_SECTION}/{re.escape(ETC2_KEY)}=.*\n", "", text, flags=re.M)
    if re.search(rf"^{re.escape(ETC2_KEY)}=true$", text, flags=re.M):
        manifest.write_text(text, encoding="utf-8")
        return False
    if re.search(rf"^{re.escape(ETC2_KEY)}=", text, flags=re.M):
        text = re.sub(rf"^{re.escape(ETC2_KEY)}=.*$", f"{ETC2_KEY}=true", text, flags=re.M)
    elif re.search(rf"^\[{ETC2_SECTION}\]$", text, flags=re.M):
        text = re.sub(rf"^\[{ETC2_SECTION}\]$",
                      f"[{ETC2_SECTION}]\n\n{ETC2_KEY}=true", text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n\n[{ETC2_SECTION}]\n\n{ETC2_KEY}=true\n"
    manifest.write_text(text, encoding="utf-8")
    print("  + enabled ETC2 ASTC in project.godot (required by macOS universal/arm64)")
    return True


def add_preset(cfg: Path, key: str, root: Path) -> str:
    """Append the platform's default preset. Only that one: a NAMED preset of a
    variant carries settings this generator knows nothing about -- a store's
    HTML shell, its own exclude list -- so a missing one is an error to fix in
    export_presets.cfg, not a block to invent."""
    platform, preset_name, path_template, _ = PLATFORMS[key]
    index = next_preset_index(cfg)
    export_path = path_template.format(name=artifact_name(root))
    slug = artifact_name(root).lower().replace("_", "-")
    bundle_id = f"com.{re.sub(r'[^a-z0-9]', '', slug) or 'game'}.game"
    options = "\n".join(f"{k}={v.format(bundle_id=bundle_id)}"
                        for k, v in PRESET_OPTIONS[key])
    block = f"""
[preset.{index}]

name="{preset_name}"
platform="{platform}"
runnable=true
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter="docs/*,tools/*,shots/*,build/*,.dev/*,.claude/*"
export_path="{export_path}"
encryption_include_filters=""
encryption_exclude_filters=""
seed=0
encrypt_pck=false
encrypt_directory=false
script_export_mode=2

[preset.{index}.options]

custom_template/debug=""
custom_template/release=""
{options}
"""
    with cfg.open("a", encoding="utf-8") as handle:
        handle.write(block)
    print(f'  + added preset "{preset_name}" -> {export_path}')
    return preset_name


def preset_export_path(cfg: Path, name: str | None) -> str | None:
    """The preset's own export_path, which wins over the generated name.

    The generated name is an ASCII slug of `config/name`, so a non-Latin project
    name lands on a different file than the preset declares. Windows hides that
    by being case-insensitive; macOS and Linux ship two artifacts instead.
    """
    body = preset_body(cfg, name)
    if body is None:
        return None
    found = re.search(r'^export_path="([^"]+)"$', body, flags=re.M)
    return found.group(1) if found and found.group(1).strip() else None


def encryption_state(cfg: Path, name: str | None) -> tuple[bool, bool]:
    """(encrypt_pck, has a custom template) for the preset called `name`."""
    body = preset_body(cfg, name)
    if body is None:
        return False, False
    encrypted = bool(re.search(r"^encrypt_pck=true$", body, flags=re.M))
    custom = re.search(r'^custom_template/release="([^"]+)"$', body, flags=re.M)
    return encrypted, bool(custom and Path(custom.group(1)).is_file())


def export(godot: str, root: Path, preset: str, out: Path, debug: bool) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    flag = "--export-debug" if debug else "--export-release"
    cmd = [godot, "--headless", "--path", str(root), flag, preset, str(out)]
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if out.exists():
        return True
    # Godot prints the reason on the line AFTER "Cannot export ... due to
    # configuration errors:", so grepping for ERROR alone hides the diagnosis.
    tail = [ln.rstrip() for ln in (result.stdout + result.stderr).splitlines()]
    for line in tail[-25:]:
        if line.strip():
            print(f"    {line}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a Godot project")
    parser.add_argument("targets", nargs="*", choices=[*PLATFORMS, []],
                        help="windows / macos / web (default: all available)")
    parser.add_argument("--project", help="project root (default: search upwards)")
    parser.add_argument("--debug", action="store_true", help="debug build")
    parser.add_argument("--list", action="store_true", help="report state and exit")
    parser.add_argument("--bump", choices=("major", "minor", "patch"),
                        help="raise config/version before exporting")
    parser.add_argument("--set-version", help="write config/version, e.g. 1.2.0")
    # One named preset of one platform. ship.py drives every variant through
    # these two, one process per export, so that project.godot can be stripped
    # differently around each one.
    parser.add_argument("--preset", help="export this preset (name= in export_presets.cfg) "
                                         "instead of the platform's first one")
    parser.add_argument("--out", help="export path, relative to the project; default: the "
                                      "preset's own export_path")
    args = parser.parse_args()
    if (args.preset or args.out) and len(args.targets) != 1:
        parser.error("--preset/--out name one export: pass exactly one platform with them")

    root = Path(args.project).resolve() if args.project else find_project(Path.cwd().resolve())
    version = project_version(root)
    if args.set_version or args.bump:
        new_version = args.set_version or bumped(version, args.bump)
        set_project_field(root, "config/version", new_version)
        print(f"version   {version} -> {new_version}")
        version = new_version
    godot = find_godot()
    engine = engine_version(godot)
    folder = templates_dir(engine)
    cfg = root / "export_presets.cfg"
    presets = read_presets(cfg)

    print(f"project   {root}  (version {version})")
    print(f"engine    {godot}  ({engine})")
    print(f"templates {folder if folder else 'NOT INSTALLED'}")
    for key, (platform, _, _, prefix) in PLATFORMS.items():
        encrypted, custom = encryption_state(cfg, presets.get(platform))
        if custom:
            ready = "custom"
        else:
            ready = "yes" if have_template(folder, prefix, args.debug) else "MISSING"
        note = "  encrypted" if encrypted else ""
        print(f"  {key:8} template: {ready:8} preset: {presets.get(platform, '-')}{note}")
    if args.list:
        return 0
    if folder is None:
        sys.exit("Export templates are not installed. In the editor: "
                 "Editor > Manage Export Templates > Download and Install.")

    stamp(root, cfg, version, args.debug)

    targets = args.targets or list(PLATFORMS)
    failed = []
    for key in targets:
        platform, default_name, path_template, prefix = PLATFORMS[key]
        print(f"\n[{key}]" + (f" {args.preset}" if args.preset else ""))
        name = args.preset or presets.get(platform)
        if name and preset_body(cfg, name) is None:
            if name != default_name:
                # A variant's preset carries what the generator cannot know; see add_preset.
                print(f'  FAILED: no preset named "{name}" in export_presets.cfg -- add it '
                      f"there (the {default_name} block is a starting point)")
                failed.append(key)
                continue
            name = None
        encrypted, custom = encryption_state(cfg, name)
        # A custom template stands in for the installed one, so a machine that
        # only ever builds its own templates needs nothing in the editor's folder.
        if not custom and not have_template(folder, prefix, args.debug):
            print("  skipped: no export template for this platform")
            failed.append(key)
            continue
        if key in NEEDS_ETC2:
            ensure_etc2_astc(root)
        # An encrypting preset on a stock template exports without a single error
        # and produces a build that cannot decrypt its own pack at startup. The
        # official templates carry an empty key; only a self-built one has it.
        if encrypted and not custom:
            print("  ! encrypt_pck=true but no custom template — the build would ship")
            print("    broken. Build one: python .claude/skills/godot-build/"
                  "build_templates.py --src <godot source> " + key)
            failed.append(key)
            continue
        if encrypted:
            print("  encryption: on (custom template)")
        name = name or add_preset(cfg, key, root)
        declared = args.out or preset_export_path(cfg, name)
        out = root / (declared or path_template.format(name=artifact_name(root)))
        if export(godot, root, name, out, args.debug):
            print(f"  OK  {out}  ({out.stat().st_size / 1048576:.1f} MB)")
        else:
            print(f"  FAILED: {out} was not produced")
            failed.append(key)

    print("\n" + ("all builds succeeded" if not failed
                  else "failed: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
