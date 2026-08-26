#!/usr/bin/env python3
"""Compile Godot export templates with the PCK encryption key baked in.

    python build_templates.py --src ../godot windows       # one platform
    python build_templates.py --src ../godot macos         # on a Mac
    python build_templates.py --list                       # state only

Encrypting a PCK needs templates built from source: the official ones carry an
empty key, and Godot then exports a build that fails to decrypt itself at run
time without ever reporting an error. This compiles them, packages each platform
the way Godot's exporter expects to find it, and points the preset at the result.

The key is read from `.keys/dev.gdkey` (git-ignored), or $SCRIPT_AES256_ENCRYPTION_KEY,
and must be the same 64 hex characters the export preset uses. A template and a
preset with different keys produce a build that starts and then cannot read its
own pack.
"""

from __future__ import annotations

import argparse
import os
import platform as host_platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "") and _stream.encoding.lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# CLI name -> (scons platform=, which host OS can build it, installed template filename)
# macOS templates need Apple's SDK and linker, so they only build on a Mac.
TARGETS = {
    "windows": ("windows", {"Windows", "Linux"}, "windows_release_x86_64.exe"),
    "macos": ("macos", {"Darwin"}, "macos.zip"),
    "linux": ("linuxbsd", {"Linux"}, "linux_release.x86_64"),
    "web": ("web", {"Windows", "Darwin", "Linux"}, "web_release.zip"),
}

# Where finished templates are kept. Outside any project, because one build
# serves every project on the same engine version, and outside the source tree,
# which is disposable.
TEMPLATE_HOME = Path(os.environ.get("GODOT_CUSTOM_TEMPLATES",
                                    Path.home() / ".godot_custom_templates"))

KEY_FILE = ".keys/dev.gdkey"
KEY_ENV = "SCRIPT_AES256_ENCRYPTION_KEY"


def find_project(start: Path) -> Path:
    for folder in [start, *start.parents]:
        if (folder / "project.godot").is_file():
            return folder
    sys.exit(f"no project.godot in {start} or any parent directory")


def read_key(root: Path) -> str:
    key_path = root / KEY_FILE
    key = ""
    if key_path.is_file():
        key = key_path.read_text(encoding="utf-8").strip()
    elif os.environ.get(KEY_ENV):
        key = os.environ[KEY_ENV].strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        sys.exit(
            f"No usable key. Put 64 hex characters in {key_path}, or set ${KEY_ENV}.\n"
            f"Generate one with:  python -c \"import secrets;print(secrets.token_hex(32))\"")
    return key.lower()


def source_version(src: Path) -> str:
    """`4.7.stable` — the string Godot uses to name its template folder."""
    text = (src / "version.py").read_text(encoding="utf-8")
    got = {k: re.search(rf'^{k}\s*=\s*"?([^"\n]+)"?', text, flags=re.M)
           for k in ("major", "minor", "patch", "status")}
    if not all(got.values()):
        sys.exit(f"cannot read a version out of {src / 'version.py'}")
    major, minor, patch, status = (m.group(1).strip() for m in got.values())
    # Godot omits a .0 patch level from the folder name: 4.7.0 -> "4.7.stable".
    core = f"{major}.{minor}" if patch == "0" else f"{major}.{minor}.{patch}"
    return f"{core}.{status}"


def editor_version(godot: str | None) -> str | None:
    if not godot or not Path(godot).is_file():
        return None
    try:
        out = subprocess.run([godot, "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip().splitlines()[-1]
    except Exception:
        return None
    match = re.match(r"(\d+\.\d+(?:\.\d+)?)\.(\w+)", out)
    return f"{match.group(1)}.{match.group(2)}" if match else out


def scons(src: Path, key: str, args: list[str]) -> None:
    env = {**os.environ, KEY_ENV: key}
    cmd = [shutil.which("scons") or "scons", *args]
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=src, env=env)
    if result.returncode != 0:
        sys.exit(f"scons failed ({result.returncode}) — see the output above")


def build_windows(src: Path, key: str, out_dir: Path) -> Path:
    scons(src, key, ["platform=windows", "target=template_release", "arch=x86_64",
                     f"-j{os.cpu_count() or 4}"])
    built = src / "bin" / "godot.windows.template_release.x86_64.exe"
    if not built.is_file():
        sys.exit(f"expected {built} to exist after the build")
    target = out_dir / "windows_release_x86_64.exe"
    shutil.copy2(built, target)
    # The console wrapper is a separate binary; copy it when the preset wants one.
    console = built.with_name("godot.windows.template_release.x86_64.console.exe")
    if console.is_file():
        shutil.copy2(console, out_dir / "windows_release_x86_64.console.exe")
    return target


def build_macos(src: Path, key: str, out_dir: Path, arch: str) -> Path:
    """Build, lipo if universal, and repackage as the .zip the exporter opens.

    Godot does not accept a bare macOS binary: it looks inside the zip for
    `macos_template.app/Contents/MacOS/godot_macos_release.<arch>`, where <arch>
    is whatever `binary_format/architecture` says. A universal preset therefore
    needs both slices merged, or the export fails with "Requested template
    binary not found".
    """
    slices = ["x86_64", "arm64"] if arch == "universal" else [arch]
    for one in slices:
        scons(src, key, ["platform=macos", "target=template_release", f"arch={one}",
                         f"-j{os.cpu_count() or 4}"])

    binaries = [src / "bin" / f"godot.macos.template_release.{one}" for one in slices]
    for binary in binaries:
        if not binary.is_file():
            sys.exit(f"expected {binary} to exist after the build")

    merged = src / "bin" / f"godot.macos.template_release.{arch}"
    if arch == "universal":
        lipo = shutil.which("lipo")
        if not lipo:
            sys.exit("lipo not found — it ships with Xcode command line tools")
        subprocess.run([lipo, "-create", *map(str, binaries), "-output", str(merged)],
                       check=True)
        print(f"  lipo -> {merged.name}")

    # Assemble the .app around the binary, starting from the skeleton in the tree.
    skeleton = src / "misc" / "dist" / "macos_template.app"
    if not skeleton.is_dir():
        sys.exit(f"missing {skeleton} — is --src really a Godot source tree?")
    staging = out_dir / "_staging"
    shutil.rmtree(staging, ignore_errors=True)
    app = staging / "macos_template.app"
    shutil.copytree(skeleton, app)
    macos_dir = app / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)
    placed = macos_dir / f"godot_macos_release.{arch}"
    shutil.copy2(merged, placed)
    # An .app whose binary is not executable installs cleanly and then will not launch.
    placed.chmod(placed.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    target = out_dir / "macos.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                entry = zipfile.ZipInfo(str(path.relative_to(staging).as_posix()))
                entry.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes())
    shutil.rmtree(staging, ignore_errors=True)
    return target


def build_linux(src: Path, key: str, out_dir: Path) -> Path:
    scons(src, key, ["platform=linuxbsd", "target=template_release", "arch=x86_64",
                     f"-j{os.cpu_count() or 4}"])
    built = src / "bin" / "godot.linuxbsd.template_release.x86_64"
    if not built.is_file():
        sys.exit(f"expected {built} to exist after the build")
    target = out_dir / "linux_release.x86_64"
    shutil.copy2(built, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return target


def build_web(src: Path, key: str, out_dir: Path) -> Path:
    if not shutil.which("emcc"):
        sys.exit("emcc not found — the Web template needs the Emscripten SDK on PATH")
    scons(src, key, ["platform=web", "target=template_release", f"-j{os.cpu_count() or 4}"])
    built = src / "bin" / "godot.web.template_release.wasm32.zip"
    if not built.is_file():
        candidates = sorted((src / "bin").glob("godot.web.template_release*.zip"))
        if not candidates:
            sys.exit("no web template zip was produced")
        built = candidates[0]
    target = out_dir / "web_release.zip"
    shutil.copy2(built, target)
    return target


def preset_block(cfg_text: str, platform: str) -> tuple[int, int] | None:
    """Character span of the `[preset.N]` block declaring this platform."""
    blocks = list(re.finditer(r"^\[preset\.(\d+)\]$", cfg_text, flags=re.M))
    for index, match in enumerate(blocks):
        start = match.start()
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(cfg_text)
        body = cfg_text[start:end]
        if re.search(rf'^platform="{re.escape(platform)}"$', body, flags=re.M):
            # Stop before the [preset.N.options] section, which holds custom_template.
            return start, end
    return None


def wire_preset(root: Path, platform: str, template: Path, encrypt: bool) -> None:
    """Point the preset at the template and turn encryption on.

    Both halves matter and neither is visible in a successful export log: a
    preset with encryption but the stock template ships a build that cannot read
    its own pack, and a custom template with encryption off is just a slower
    normal build.
    """
    cfg = root / "export_presets.cfg"
    text = cfg.read_text(encoding="utf-8")
    span = preset_block(text, platform)
    if span is None:
        print(f"  ! no preset for {platform} in export_presets.cfg — run build.py first")
        return
    start, end = span
    body = text[start:end]

    posix = template.resolve().as_posix()
    if re.search(r'^custom_template/release=.*$', body, flags=re.M):
        body = re.sub(r'^custom_template/release=.*$',
                      f'custom_template/release="{posix}"', body, flags=re.M)
    else:
        body = re.sub(r'^(\[preset\.\d+\.options\]\n)',
                      rf'\1\ncustom_template/release="{posix}"', body, count=1, flags=re.M)

    if encrypt:
        for key, value in (("encrypt_pck", "true"), ("encrypt_directory", "true"),
                           ("encryption_include_filters", '"*"')):
            if re.search(rf'^{key}=.*$', body, flags=re.M):
                body = re.sub(rf'^{key}=.*$', f"{key}={value}", body, flags=re.M)

    cfg.write_text(text[:start] + body + text[end:], encoding="utf-8")
    print(f"  wired preset: custom_template/release -> {posix}"
          + (" (encryption on)" if encrypt else ""))


def preset_arch(root: Path, platform: str) -> str:
    text = (root / "export_presets.cfg").read_text(encoding="utf-8")
    span = preset_block(text, platform)
    if span:
        match = re.search(r'^binary_format/architecture="([^"]+)"$',
                          text[span[0]:span[1]], flags=re.M)
        if match:
            return match.group(1)
    return "universal"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Godot export templates with a baked-in key")
    parser.add_argument("targets", nargs="*", choices=[*TARGETS, []],
                        help="windows / macos / linux / web (default: what this host can build)")
    parser.add_argument("--src", help="Godot source tree (default: $GODOT_SRC)")
    parser.add_argument("--project", help="project root (default: search upwards)")
    parser.add_argument("--out", help=f"template output dir (default: {TEMPLATE_HOME})")
    parser.add_argument("--no-encrypt", action="store_true",
                        help="build and wire the template, but leave encryption off")
    parser.add_argument("--list", action="store_true", help="report state and exit")
    args = parser.parse_args()

    root = Path(args.project).resolve() if args.project else find_project(Path.cwd().resolve())
    host = host_platform.system()
    out_dir = Path(args.out).resolve() if args.out else TEMPLATE_HOME

    src_raw = args.src or os.environ.get("GODOT_SRC")
    src = Path(src_raw).resolve() if src_raw else None
    src_ok = bool(src and (src / "SConstruct").is_file() and (src / "version.py").is_file())

    print(f"project    {root}")
    print(f"host       {host}")
    print(f"source     {src if src else 'NOT SET'}" + ("" if src_ok else "   <- not a Godot source tree"))
    print(f"templates  {out_dir}")
    if src_ok:
        version = source_version(src)
        editor = editor_version(os.environ.get("GODOT") or os.environ.get("GODOT_EXE"))
        print(f"version    {version}" + (f"   (editor reports {editor})" if editor else ""))
        # A template from a different engine version loads and then misbehaves in
        # ways that look like project bugs, so this is worth failing loudly over.
        if editor and editor != version:
            print(f"  ! MISMATCH: build templates from the {editor} sources, not {version}")
    print(f"key        {root / KEY_FILE}"
          + ("  (present)" if (root / KEY_FILE).is_file() else "  MISSING"))
    for name, (_, hosts, filename) in TARGETS.items():
        buildable = "yes" if host in hosts else f"needs {'/'.join(sorted(hosts))}"
        have = (out_dir / filename).is_file()
        print(f"  {name:8} buildable here: {buildable:20} built: {'yes' if have else 'no'}")
    if args.list:
        return 0

    if not src_ok:
        sys.exit("Point --src at a Godot source tree (git clone https://github.com/godotengine/godot),\n"
                 "checked out at the tag matching your editor version.")
    if not shutil.which("scons"):
        sys.exit("scons not found — pip install scons")

    key = read_key(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = args.targets or [n for n, (_, hosts, _) in TARGETS.items() if host in hosts]

    failed = []
    for name in targets:
        _, hosts, _ = TARGETS[name]
        print(f"\n[{name}]")
        if host not in hosts:
            print(f"  skipped: this platform can only be built on {'/'.join(sorted(hosts))}")
            failed.append(name)
            continue
        try:
            if name == "windows":
                built = build_windows(src, key, out_dir)
                wire_preset(root, "Windows Desktop", built, not args.no_encrypt)
            elif name == "macos":
                built = build_macos(src, key, out_dir, preset_arch(root, "macOS"))
                wire_preset(root, "macOS", built, not args.no_encrypt)
            elif name == "linux":
                built = build_linux(src, key, out_dir)
                wire_preset(root, "Linux", built, not args.no_encrypt)
            else:
                built = build_web(src, key, out_dir)
                # Encryption is pointless on the Web: the key sits in the .wasm the
                # browser downloads, and public tools read it straight out.
                wire_preset(root, "Web", built, False)
            print(f"  OK  {built}  ({built.stat().st_size / 1048576:.1f} MB)")
        except SystemExit as stop:
            print(f"  FAILED: {stop}")
            failed.append(name)

    print("\n" + ("templates ready" if not failed else "failed: " + ", ".join(failed)))
    print("Export with build.py; the key in the preset and in the template must match.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
