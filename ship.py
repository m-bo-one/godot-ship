#!/usr/bin/env python3
"""godot-ship — export a Godot project, and prove the export is worth handing over.

    py ship.py init                # set a project up: configs, .gitignore, hook
    py ship.py key                 # create the pack encryption key, once
    py ship.py doctor              # what is installed, configured and missing
    py ship.py build               # every target in godot-ship.json
    py ship.py build web           # one target
    py ship.py serve               # open the web export over HTTP
    py ship.py audit               # what is actually inside the pack
    py ship.py boot                # run the exported artifact and read its output
    py ship.py check-paths         # machine paths about to be committed
    py ship.py templates windows --src <godot source>

Everything project-specific lives in `godot-ship.json` (tracked) and
`.godot-ship.local.json` (git-ignored, machine paths only). Nothing in this
directory knows the name of any project or of any machine.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import audit as audit_lib      # noqa: E402
from lib import config as config_lib    # noqa: E402
from lib import gdmaim as gdmaim_lib    # noqa: E402
from lib import paths as paths_lib      # noqa: E402
from lib import review as review_lib    # noqa: E402
from lib import rules                   # noqa: E402
from lib import serve as serve_lib      # noqa: E402
from lib import yamlish                 # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "") and _stream.encoding.lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

GREEN, RED, YELLOW, CYAN, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
if os.name == "nt":
    os.system("")  # turn on ANSI in a legacy console; harmless in a modern one

ARTIFACT = {
    "windows": "build/{name}.exe",
    "macos": "build/{name}.zip",
    "web": "build/web/index.html",
}

failures: list[str] = []


# Every line is flushed: the exporter is a subprocess writing straight to the
# console, and buffered prints here would arrive after its output, out of order.
def step(title: str) -> None:
    print(f"\n{CYAN}== {title}{OFF}", flush=True)


def ok(msg: str) -> None:
    print(f"   {GREEN}ok{OFF}   {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"   {YELLOW}warn{OFF} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"   {RED}FAIL{OFF} {msg}", flush=True)
    failures.append(msg)


def artifact_name(root: Path) -> str:
    manifest = root / "project.godot"
    if not manifest.is_file():
        return root.name   # `review` and `check-paths` are useful outside a game too
    text = manifest.read_text(encoding="utf-8")
    match = re.search(r'^config/name="([^"]*)"', text, flags=re.M)
    raw = match.group(1) if match else root.name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or root.name


def artifact_path(cfg: config_lib.Config, target: str) -> Path:
    """The preset's own export_path wins; the generated name is the fallback."""
    presets = cfg.root / "export_presets.cfg"
    platform = {"windows": "Windows Desktop", "macos": "macOS", "web": "Web"}[target]
    if presets.is_file():
        text = presets.read_text(encoding="utf-8")
        blocks = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
        for index, match in enumerate(blocks):
            end = blocks[index + 1].start() if index + 1 < len(blocks) else len(text)
            body = text[match.start():end]
            if re.search(rf'^platform="{re.escape(platform)}"$', body, flags=re.M):
                found = re.search(r'^export_path="([^"]+)"$', body, flags=re.M)
                if found and found.group(1).strip():
                    return _inside_project(cfg, found.group(1))
    return cfg.root / ARTIFACT[target].format(name=artifact_name(cfg.root))


def _inside_project(cfg: config_lib.Config, declared: str) -> Path:
    """`root / declared`, but only when that is still under the root.

    Joining an absolute path throws the left operand away: the project root and a
    full path to any system binary join to that binary alone, and `..` walks out
    just as well. This path is not only written to -- `boot` EXECUTES it, `serve`
    publishes its directory over HTTP and `audit` reads it -- so an export_path
    that leaves the project is refused loudly. It is not quietly replaced with
    the generated name either: somebody exporting to a deliberate output
    directory would find their build had moved without a word being said.
    """
    out = cfg.root / declared
    root = cfg.root.resolve()
    if not out.resolve().is_relative_to(root):
        raise SystemExit(
            f'export_path="{declared}" in export_presets.cfg resolves to {out.resolve()},\n'
            f"  which is outside {cfg.root}. ship.py runs, serves and reads that path,\n"
            "  so it must stay inside the project. Make it relative to the project root.")
    return out


def engine_version(engine: str) -> str:
    out = subprocess.run([engine, "--version"], capture_output=True, text=True,
                         timeout=60).stdout.strip().splitlines()[-1]
    return out


def template_carries_key(template: Path, key: str) -> bool:
    """The exporter takes the key from the environment, the finished game only has
    the one compiled into its template, and nothing compares them: a mismatch
    exports without an error and ships a build that cannot read its own pack."""
    raw = bytes.fromhex(key)
    blob = template.read_bytes()
    return raw in blob


# --------------------------------------------------------------------- preflight

def preflight(cfg: config_lib.Config, targets: list[str]) -> str:
    step("Preflight")
    engine = cfg.engine
    if not engine:
        want = cfg.get("engine_version")
        seen = "\n".join(f"    {v:32} {p}  ({s})" for p, s, v in cfg.engine_report()) or "    (none)"
        refused = "".join(f"\n  refused: {note}" for note in cfg.refused)
        raise SystemExit(
            f"no engine matching {want or 'any version'}. Seen:\n{seen}{refused}\n"
            f'  Put the right one in {config_lib.LOCAL} as "engine", or set $GODOT_SHIP_ENGINE.')
    ok(f"engine {engine_version(engine)}")

    for target in targets:
        if not cfg.encrypts(target):
            continue
        key = cfg.key()
        template = cfg.template(target)
        if not key:
            fail(f"{target} encrypts but no key at {cfg.key_path()} (and $GODOT_SCRIPT_ENCRYPTION_KEY is unset)")
        elif not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            fail(f"the key is not 64 hex characters")
        elif not template:
            fail(f"{target} encrypts but names no custom template in .godot-ship.local.json -- "
                 "an official template carries an EMPTY key and the build cannot read its own pack")
        elif not template_carries_key(Path(template), key):
            fail(f"the {target} template does not carry this key -- rebuild it: "
                 f"py ship.py templates {target} --src <godot source>")
        else:
            ok(f"{target}: template carries the same key the export will use")
    if failures:
        raise SystemExit(f"{RED}preflight failed:{OFF}\n  - " + "\n  - ".join(failures))
    return engine


# ------------------------------------------------------------------------ build

def patch_presets(cfg: config_lib.Config, targets: list[str]) -> str | None:
    """Write the machine's template paths into the preset for the length of the
    export. They are absolute paths belonging to one computer; committed, they
    break every other checkout and publish a user name. The tracked file keeps
    custom_template/release empty and this puts it back."""
    presets = cfg.root / "export_presets.cfg"
    if not presets.is_file():
        return None
    original = presets.read_text(encoding="utf-8")
    text = original
    for target in targets:
        template = cfg.template(target)
        platform = {"windows": "Windows Desktop", "macos": "macOS", "web": "Web"}[target]
        blocks = list(re.finditer(r"^\[preset\.(\d+)\]$", text, flags=re.M))
        for index, match in enumerate(blocks):
            end = blocks[index + 1].start() if index + 1 < len(blocks) else len(text)
            body = text[match.start():end]
            if not re.search(rf'^platform="{re.escape(platform)}"$', body, flags=re.M):
                continue
            if template:
                body = re.sub(r'^custom_template/release=".*"$',
                              f'custom_template/release="{Path(template).as_posix()}"',
                              body, flags=re.M)
            # godot-ship.yaml is the authority on whether a target encrypts, and
            # the preset is where Godot reads it. Disagreeing silently means an
            # export that looks encrypted in the config and is not in the file.
            wanted = "true" if cfg.encrypts(target) else "false"
            was = re.search(r"^encrypt_pck=(\w+)$", body, flags=re.M)
            for key in ("encrypt_pck", "encrypt_directory"):
                body = re.sub(rf"^{key}=\w+$", f"{key}={wanted}", body, flags=re.M)
            if cfg.encrypts(target):
                body = re.sub(r'^encryption_include_filters=".*"$',
                              'encryption_include_filters="*"', body, flags=re.M)
            if was and was.group(1) != wanted:
                warn(f"{target}: the preset said encrypt_pck={was.group(1)} and the config says "
                     f"{wanted} -- exporting as the config says; fix the preset")
            text = text[:match.start()] + body + text[end:]
            break
    if text != original:
        presets.write_text(text, encoding="utf-8")
    return original


def strip_project(cfg: config_lib.Config) -> str | None:
    """Take dev autoloads and editor plugins out for the length of the export.

    Both halves or neither: excluding an addon's files while its autoload stays
    in project.godot leaves an autoload pointing at nothing, and the player's
    first frame is three ERROR lines. Removing the autoload alone does not last
    either -- the plugin writes it back every time the editor loads.
    """
    rules = cfg.get("strip") or {}
    autoloads = rules.get("autoloads") or []
    plugins = rules.get("plugins") or []
    if not autoloads and not plugins:
        return None
    manifest = cfg.root / "project.godot"
    original = manifest.read_text(encoding="utf-8")
    text = original
    for name in autoloads:
        text = re.sub(rf"(?m)^{re.escape(name)}=.*\r?\n", "", text)
    for plugin in plugins:
        text = text.replace(f'"{plugin}", ', "").replace(f', "{plugin}"', "")
        text = text.replace(f'"{plugin}"', "")
    if text == original:
        return None
    manifest.write_text(text, encoding="utf-8")
    ok(f"held out of this export: {', '.join(autoloads + plugins)}")
    return original


def carry_payload(cfg: config_lib.Config, engine: str, out: Path) -> None:
    """Runtime libraries that the exporter does not carry and git does not hold.

    Without them the build starts and quietly renders on whatever the player's
    system happens to provide -- a silent downgrade nobody reports as a bug.
    """
    rules = cfg.get("payload") or {}
    sources: list[Path] = []
    engine_dir = Path(engine).parent
    for pattern in rules.get("from_engine_dir") or []:
        sources += sorted(engine_dir.glob(pattern))
    for pattern in rules.get("from_project") or []:
        if ".." in Path(pattern).parts:
            # Not refused -- a vendor SDK really can sit beside the project --
            # but said out loud: whatever this picks up lands next to the exe and
            # goes into the archive with it.
            warn(f"payload.from_project {pattern!r} reaches outside the project; "
                 "those files travel with the build")
        sources += sorted(cfg.root.glob(pattern))
    if not sources:
        return
    copied = 0
    for source in sources:
        target = out.parent / source.name
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
            copied += 1
    expected = rules.get("expect_count")
    if expected and len(sources) < expected:
        fail(f"payload is {len(sources)} file(s), {expected} expected -- "
             "a build short of them runs and silently loses what they provide")
    else:
        ok(f"payload: {len(sources)} file(s) beside the build ({copied} refreshed)")


def archive(cfg: config_lib.Config, out: Path) -> None:
    """Named entries, never the folder: an older build's exe, a leftover .dev or
    a stray screenshot sitting beside the artifacts would ride along unnoticed.
    The source map is deliberately not in here -- it belongs BESIDE an archive
    handed over, never inside it."""
    import zipfile
    manifest = cfg.root / "project.godot"
    version = re.search(r'^config/version="([^"]*)"', manifest.read_text(encoding="utf-8"),
                        flags=re.M) if manifest.is_file() else None
    stem = out.stem + (f"-{version.group(1)}" if version and version.group(1).strip() else "")
    zip_path = out.parent / f"{stem}.zip"
    entries = [out]
    pack = out.with_suffix(".pck")
    if pack.is_file():
        entries.append(pack)
    entries += sorted(out.parent.glob("*.dll"))
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as handle:
        for entry in entries:
            handle.write(entry, entry.name)
    raw = sum(entry.stat().st_size for entry in entries)
    ok(f"{zip_path.name}: {len(entries)} files, "
       f"{zip_path.stat().st_size / 1048576:.0f} MB from {raw / 1048576:.0f} MB")


def build(cfg: config_lib.Config, targets: list[str], debug: bool = False) -> None:
    engine = preflight(cfg, targets)
    if cfg.get("obfuscate"):
        # A new call site with no lock entry breaks in the exported build and
        # nowhere else, so it stops the build rather than shipping.
        obfuscate(cfg, check=True)
        if failures:
            verdict()
    os.environ["GODOT"] = engine
    key = cfg.key() if any(cfg.encrypts(t) for t in targets) else None
    if key:
        os.environ["GODOT_SCRIPT_ENCRYPTION_KEY"] = key

    step("Export")
    started = time.time()
    # Inside the try, both of them: patch_presets has already rewritten
    # export_presets.cfg by the time strip_project runs, and strip_project reads
    # and writes project.godot -- either can raise. Outside, the `finally` that
    # puts the files back would never run, stranding this machine's absolute
    # template path in a tracked file, which is the one thing this tool exists
    # to keep out of a commit.
    restore = restore_project = None
    try:
        restore = patch_presets(cfg, targets)
        restore_project = strip_project(cfg)
        cmd = [sys.executable, str(HERE / "lib" / "build.py"), "--project", str(cfg.root)]
        if debug:
            cmd.append("--debug")
        cmd += targets
        if subprocess.run(cmd).returncode != 0:
            raise SystemExit("the export failed")
    finally:
        if restore is not None:
            (cfg.root / "export_presets.cfg").write_text(restore, encoding="utf-8")
        if restore_project is not None:
            (cfg.root / "project.godot").write_text(restore_project, encoding="utf-8")
        os.environ.pop("GODOT_SCRIPT_ENCRYPTION_KEY", None)

    step("What came out")
    for target in targets:
        out = artifact_path(cfg, target)
        if not out.exists():
            fail(f"{target}: {out} was not produced")
            continue
        if target == "web":
            missing = [f for f in ("index.js", "index.wasm", "index.pck")
                       if not (out.parent / f).is_file()]
            if missing:
                fail(f"web build is missing {', '.join(missing)}")
            else:
                total = sum(f.stat().st_size for f in out.parent.iterdir() if f.is_file())
                ok(f"web: {total / 1048576:.1f} MB, {len(list(out.parent.iterdir()))} files")
                warn("index.html from disk fails with \"Failed to fetch\" -- open it with: py ship.py serve")
            continue
        pack = out.with_suffix(".pck")
        if pack.is_file():
            kb = pack.stat().st_size / 1024
            floor = cfg["boot"]["min_pack_kb"]
            # An encrypting export with no key in the environment still writes a
            # pack: a header and nothing else. Size is the only cheap tell.
            if kb < floor:
                fail(f"the pack is {kb:.0f} KB, under {floor} -- the export ran without the encryption key")
            else:
                ok(f"{out.name} {out.stat().st_size / 1048576:.1f} MB + pack {kb:.0f} KB")
        else:
            ok(f"{out.name} {out.stat().st_size / 1048576:.1f} MB (pack embedded)")
        carry_payload(cfg, engine, out)

    desktop = [t for t in targets if t != "web"]
    if cfg.get("obfuscate") and not failures and desktop:
        _carry_source_map(cfg, desktop[0], started)
    if cfg.get("archive") and not failures and desktop:
        archive(cfg, artifact_path(cfg, desktop[0]))

    if not failures:
        boot(cfg)
    verdict()


# ------------------------------------------------------------------------- boot

def boot(cfg: config_lib.Config) -> None:
    """A file appearing on disk is not a build. Run it the way the project's own
    rules define a clean run, and read what it says."""
    settings = cfg["boot"]
    target = settings.get("target")
    if not target or target not in cfg.targets:
        return
    exe = artifact_path(cfg, target)
    if not exe.is_file():
        return
    step("Boot the exported build")
    result = subprocess.run([str(exe), *settings["args"]], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=600)
    log = (result.stdout or "") + (result.stderr or "")
    bad = [ln.strip() for ln in log.splitlines()
           if any(marker in ln for marker in settings["reject"])]
    if bad:
        fail(f"{len(bad)} rejected line(s) in the exported build:")
        for line in bad[:6]:
            print(f"        {line}")
    else:
        ok("clean run: " + ", ".join(f"no {m}" for m in settings["reject"]))
    if result.returncode != 0:
        fail(f"the exported build exited {result.returncode}")
    else:
        ok("exit 0")
    for pattern in settings.get("expect", []):
        # Multiline: an `expect` is written against a line of the log, and `^`
        # without it silently only ever matches the very first character.
        if re.search(pattern, log, re.M):
            ok(f"matched /{pattern}/")
        else:
            fail(f"expected /{pattern}/ in the output and it is not there")


def verdict() -> None:
    step("Verdict")
    if failures:
        print(f"{RED}NOT shippable:{OFF}\n  - " + "\n  - ".join(failures))
        raise SystemExit(1)
    print(f"{GREEN}build is good{OFF}")


# ------------------------------------------------------------------------ other

def doctor(cfg: config_lib.Config) -> int:
    print(f"project   {cfg.root}")
    print(f"config    {config_lib.TRACKED}: "
          f"{'yes' if (cfg.root / config_lib.TRACKED).is_file() else 'MISSING'}   "
          f"{config_lib.LOCAL}: "
          f"{'yes' if (cfg.root / config_lib.LOCAL).is_file() else 'MISSING'}")
    want = cfg.get("engine_version")
    engine = cfg.engine
    print(f"engine    {engine or RED + 'NONE matching ' + str(want) + OFF}"
          + (f"   (project wants {want}.x)" if want else ""))
    for path, source, version in cfg.engine_report():
        mark = "->" if path == engine else "  "
        skip = "" if not want or version.startswith(want) else f"  {YELLOW}skipped{OFF}"
        print(f"       {mark} {version:34} {path}  ({source}){skip}")
    for note in cfg.refused:
        print(f"       {YELLOW}refused{OFF} {note}")
    key = cfg.key()
    print(f"key       {cfg.key_path()}: {'present' if key else 'absent'}")
    for target in cfg.targets:
        template = cfg.template(target)
        enc = "encrypted" if cfg.encrypts(target) else "plain"
        print(f"  {target:8} {enc:10} template: {template or 'official'}")
        if cfg.encrypts(target) and template and key:
            carries = template_carries_key(Path(template), key)
            print(f"           {'' if carries else RED}template carries the key: "
                  f"{carries}{OFF if not carries else ''}")
    return 0


def review(cfg: config_lib.Config) -> int:
    """Audit the setup, not the build: what is NOT covered, and what leaks.

    `audit --check` says whether anything on the forbidden list is in the pack.
    A clean answer over an incomplete list reads exactly like a clean build, so
    this asks the other half of the question.
    """
    root = cfg.root
    presets = root / "export_presets.cfg"
    preset_text = presets.read_text(encoding="utf-8") if presets.is_file() else ""
    filters = " , ".join(re.findall(r'(?m)^exclude_filter="([^"]*)"$', preset_text))

    step("Setup")
    have_tracked, have_local = config_lib.which_files(root)
    ok(f"config: {have_tracked}") if have_tracked else fail("no godot-ship.yaml -- run: ship.py init")
    ok(f"machine: {have_local}") if have_local else warn("no local config; relying on environment")
    for target in cfg.targets:
        if cfg.encrypts(target) and not cfg.template(target):
            fail(f"{target} encrypts with no custom template: the build cannot read its own pack")
        # The preset is where Godot reads it; the config is where the project
        # states it. Drift between them exports a build nobody asked for.
        platform = {"windows": "Windows Desktop", "macos": "macOS", "web": "Web"}[target]
        blocks = list(re.finditer(r"^\[preset\.(\d+)\]$", preset_text, flags=re.M))
        for index, match in enumerate(blocks):
            end = blocks[index + 1].start() if index + 1 < len(blocks) else len(preset_text)
            body = preset_text[match.start():end]
            if not re.search(rf'^platform="{re.escape(platform)}"$', body, flags=re.M):
                continue
            says = re.search(r"^encrypt_pck=(\w+)$", body, flags=re.M)
            if says and (says.group(1) == "true") != cfg.encrypts(target):
                fail(f"{target}: preset says encrypt_pck={says.group(1)}, config says "
                     f"{str(cfg.encrypts(target)).lower()} -- make the preset agree")
            break
    if cfg.encrypts("web"):
        fail("web must not encrypt: the key ships inside the .wasm the browser downloads")
    compute = review_lib.compute_shaders(root)
    if compute and "web" in cfg.targets:
        # A warning, not a refusal: the rest of the game may be exactly what the
        # web build is for. But the failure is silent -- the export succeeds and
        # the page loads -- so it has to be said out loud every time.
        warn(f"web is a target and this project has {len(compute)} compute shader(s) "
             f"({compute[0]}) -- the web renderer has no compute stage, so those "
             "will not run there. Everything else will.")

    step("Coverage")
    missed = review_lib.uncovered(root, cfg["audit"]["forbidden"], filters)
    if missed:
        for name, why in missed:
            warn(f"{name:28} {why}")
        print("        Nothing above is named by audit.forbidden or by any exclude_filter.")
    else:
        ok("every development-looking entry at the top of the tree is named somewhere")

    # A project's own `audit.forbidden` REPLACES the built-in one rather than
    # extending it -- deliberately, because the list is the project's own
    # specification. But the entries it drops live in lib/config.py, not in the
    # file being edited, so nothing on screen said the net had got smaller.
    dropped = [p for p in config_lib.DEFAULTS["audit"]["forbidden"]
               if p not in cfg["audit"]["forbidden"]]
    if dropped:
        warn(f"{len(dropped)} built-in forbidden pattern(s) are not in this project's list, "
             f"so they are NOT enforced: {', '.join(dropped[:8])}"
             + (f" and {len(dropped) - 8} more" if len(dropped) > 8 else ""))

    addons = review_lib.editor_addons(root)
    listed = [a for a in addons if any(fnmatch.fnmatch(a + "/x", p)
                                       for p in cfg["audit"]["forbidden"])]
    for addon in addons:
        if addon in listed:
            ok(f"{addon} is an editor addon and is excluded")
        else:
            warn(f"{addon} carries an EditorPlugin and is NOT excluded -- editor code in a player's build")

    for name, path in review_lib.autoloads(root):
        target = path.removeprefix("res://")
        if any(fnmatch.fnmatch(target, p) for p in cfg["audit"]["forbidden"]):
            fail(f"autoload {name} points at {target}, which is excluded -- "
                 "the autoload survives in project.godot and the first frame is ERROR lines. "
                 "Put it in `strip.autoloads`.")
    if cfg.get("obfuscate"):
        _, missing, _ = gdmaim_lib.locks(root, cfg["obfuscation"]["scan"])
        ok("every string-reached symbol is locked") if not missing else \
            fail(f"{len(missing)} string-reached symbol(s) unlocked: {', '.join(missing[:8])}")

    step("What the artifact gives away")
    key = cfg.key()
    checked = False
    for target in cfg.targets:
        out = artifact_path(cfg, target)
        pack = out.parent / "index.pck" if target == "web" else out.with_suffix(".pck")
        for artifact, secret in ((pack, key), (out, None)):
            if not artifact.is_file():
                continue
            checked = True
            found, remarks = review_lib.leaks(artifact, secret)
            for what, offset in found:
                fail(f"{artifact.name}: {what} readable at 0x{offset:x}")
            if not found:
                ok(f"{artifact.name}: no home paths, credentials or keys readable")
            for remark in remarks:
                print(f"        {artifact.name}: {remark}")
    if checked and key:
        warn("the pack key is compiled into the exe by design -- encryption stops the "
             "curious, not the determined")
    if not checked:
        warn("no artifacts on disk to read -- run a build first")

    step("Path hygiene")
    rules = cfg["paths"]
    try:
        found, unreadable = paths_lib.scan(root, rules["skip"], rules["allow"], staged=False)
    except paths_lib.GitUnavailable as why:
        # Never "clean": nothing was read. This used to print the clean line and
        # keep the verdict green outside a repository.
        fail(f"the path check did NOT run -- git could not answer: {why}")
        found, unreadable = [], []
    else:
        if found:
            for finding in found[:10]:
                fail(str(finding))
        else:
            ok("no absolute machine path in anything tracked")
    if unreadable:
        warn(f"{len(unreadable)} file(s) could not be read and were not checked: "
             + ", ".join(unreadable[:5]))
    if rules["allow"]:
        warn(f"{len(rules['allow'])} allow rule(s) in force -- each one is a check switched off")

    step("Verdict")
    if failures:
        print(f"{RED}{len(failures)} thing(s) to fix:{OFF}\n  - " + "\n  - ".join(failures))
        return 1
    print(f"{GREEN}setup is sound{OFF}")
    return 0


def check_paths(cfg: config_lib.Config, staged: bool) -> int:
    rules = cfg["paths"]
    where = "staged for commit" if staged else "tracked in the tree"
    try:
        found, unreadable = paths_lib.scan(cfg.root, rules["skip"], rules["allow"], staged=staged)
    except paths_lib.GitUnavailable as why:
        # Refuse rather than pass: this is a gate, and nothing was read.
        print(f"{RED}the path check did not run:{OFF} {why}")
        print("Nothing was read, so nothing is cleared. Run this inside the repository.")
        return 1
    for path in unreadable:
        print(f"{YELLOW}not checked{OFF} {path}  (could not be read)")
    if not found:
        print(f"no absolute machine paths in what is {where}"
              + (f" ({len(unreadable)} file(s) unreadable)" if unreadable else ""))
        return 0
    print(f"{RED}absolute machine paths in what is {where}:{OFF}")
    for finding in found:
        print(f"  {finding}")
    print("\nA path from one computer breaks every other checkout and publishes a user name.")
    print(f"Move it to {config_lib.LOCAL}, or add a regex to paths.allow in {config_lib.TRACKED}.")
    return 1


def make_key(cfg: config_lib.Config, force: bool = False) -> int:
    """Create the pack key, once.

    An existing key is never replaced without being asked twice: a new one makes
    every build ever shipped with the old one unreadable, and the export template
    has to be recompiled around it before anything can be built at all.
    """
    import secrets
    path = cfg.key_path()
    step("Pack key")
    # `key:` is a path from a config file, and this writes a secret to it.
    # `root / "/tmp/shared/x"` is `/tmp/shared/x` -- pathlib drops the root for an
    # absolute value -- so without this the key could be written anywhere the
    # user can write, and the relative_to() below would then raise AFTER the file
    # existed.
    if not path.resolve().is_relative_to(cfg.root.resolve()):
        raise SystemExit(f"key: {path} is outside the project ({cfg.root}). "
                         "The pack key is written there; keep it inside the tree.")
    if path.is_file() and not force:
        current = path.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", current):
            ok(f"{path.relative_to(cfg.root).as_posix()} is already there -- keeping it")
            print("        Replacing it orphans every build made with the old one and needs")
            print("        the template rebuilt. `ship.py key --force` if that is the intent.")
            return 0
        fail(f"{path} exists but is not 64 hex characters -- move it aside or pass --force")
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    # 0600 from the first byte rather than a chmod afterwards, which would leave
    # the key world-readable for the length of the write. A no-op on Windows.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(handle, "w", encoding="ascii") as file:
        file.write(secrets.token_hex(32))
    relative = path.relative_to(cfg.root)
    if path.parent != cfg.root:
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    ok(f"wrote {relative.as_posix()} (64 hex characters, owner-only)")

    ignore = cfg.root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    # The rule follows the key. `key:` moves the file, and a hardcoded `.keys/`
    # here would ignore an empty directory while the secret sat in a tracked one
    # -- with an "ok" printed over it.
    entry = ".keys/" if relative.parts[:1] == (".keys",) else relative.as_posix()
    have = {line.strip().strip("/") for line in lines if line.strip()}
    if entry.strip("/") not in have:
        ignore.write_text("\n".join(lines + [entry]) + "\n", encoding="utf-8")
        ok(f"{entry} added to .gitignore")
    if config_lib.is_tracked(cfg.root, relative.as_posix()):
        fail(f"{relative.as_posix()} is ALREADY TRACKED by git -- .gitignore does not "
             f"untrack it. Run: git rm --cached {relative.as_posix()}")

    warn("back this file up somewhere that is not this repository -- losing it means "
         "no future build can read a pack the old one encrypted")
    print("\n  Next, and nothing can be built encrypted until it is done:")
    print("    py ship.py templates windows --src <godot source at your engine's tag>")
    print("  An official template carries an EMPTY key: an encrypting preset on one")
    print("  exports without an error and ships a game that cannot read its own pack.")
    return 0


def obfuscate(cfg: config_lib.Config, regenerate: bool = False, check: bool = False) -> int:
    """Install GDMaim if it is absent, write the settled settings, and keep the
    lock list of string-reached symbols in step with the code."""
    root = cfg.root
    step("Obfuscation")
    if not check:
        gdmaim_lib.install(root, cfg.local.get("gdmaim_src"))
        gdmaim_lib.enable_plugin(root)
        gdmaim_lib.write_settings(root)
        _script_export_mode(root, 0)
        _exclude_gdmaim(root)
        _turn_on(cfg)
    if not gdmaim_lib.installed(root):
        fail("addons/gdmaim is not installed -- run: ship.py obfuscate")
        return 1

    needed, missing, stale = gdmaim_lib.locks(root, cfg["obfuscation"]["scan"])
    if regenerate:
        path = gdmaim_lib.write_locks(root, needed)
        ok(f"{len(needed)} string-reached symbols written to {path.relative_to(root).as_posix()}")
    elif missing:
        fail(f"{len(missing)} symbol(s) reached by string and not locked -- "
             f"run: ship.py obfuscate --locks\n        " + ", ".join(missing[:12]))
    else:
        ok(f"{len(needed)} string-reached symbols, all locked")
    if stale:
        warn(f"the lock list keeps {len(stale)} name(s) nothing reaches by string any more")
    return 1 if failures else 0


def _turn_on(cfg: config_lib.Config) -> None:
    """Flip `obfuscate` in the tracked config.

    Installing the addon and leaving the flag false is the worst of both: the
    export obfuscates, because the plugin is enabled and Godot runs it, while the
    build skips the lock scan that stops an unlocked call site from shipping.
    """
    name, _ = config_lib.which_files(cfg.root)
    path = cfg.root / (name or config_lib.TRACKED)
    if not path.is_file() or path.suffix == ".json":
        warn(f"set obfuscate: true in {path.name} so the build runs the lock scan")
        return
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^obfuscate:\s*true\b", text):
        return
    if re.search(r"(?m)^obfuscate:\s*false\b", text):
        text = re.sub(r"(?m)^obfuscate:\s*false\b", "obfuscate: true", text, count=1)
    else:
        text = text.rstrip("\n") + "\n\nobfuscate: true\n"
    path.write_text(text, encoding="utf-8")
    ok(f"obfuscate: true in {path.name} -- the build now refuses an unlocked call site")


def _script_export_mode(root: Path, mode: int) -> None:
    """0 = plain text, which is what GDMaim needs: Godot's own tokenizer would
    hand it a .gdc with nothing left to rewrite."""
    presets = root / "export_presets.cfg"
    if not presets.is_file():
        return
    text = presets.read_text(encoding="utf-8")
    fixed = re.sub(r"(?m)^script_export_mode=\d+$", f"script_export_mode={mode}", text)
    if fixed != text:
        presets.write_text(fixed, encoding="utf-8")
        print(f"  script_export_mode={mode} in every preset")


def _exclude_gdmaim(root: Path) -> None:
    """The obfuscator's own source next to the obfuscated build reads as the key
    next to the lock."""
    presets = root / "export_presets.cfg"
    if not presets.is_file():
        return
    text = presets.read_text(encoding="utf-8")
    changed = False
    for match in list(re.finditer(r'(?m)^exclude_filter="([^"]*)"$', text)):
        current = match.group(1)
        if "addons/gdmaim" in current:
            continue
        joined = (current + "," if current.strip() else "") + "addons/gdmaim/*"
        text = text[:match.start()] + f'exclude_filter="{joined}"' + text[match.end():]
        changed = True
    if changed:
        presets.write_text(text, encoding="utf-8")
        print("  addons/gdmaim/* added to every exclude_filter")


def _carry_source_map(cfg: config_lib.Config, target: str, since: float) -> None:
    """The map is the only way to read a crash report from an obfuscated build,
    and GDMaim rotates its folder at ten files -- so the one belonging to these
    artifacts is copied beside them, under the artifact's own name. It travels
    WITH an archive handed over and never inside it."""
    folder = cfg.root / ".dev/gdmaim"
    maps = sorted(folder.glob("*.gd.map"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    if not maps:
        fail("GDMaim wrote no source map -- was the obfuscation actually on?")
        return
    # The newest map in the folder is not this build's map unless this build
    # wrote it. Copying an older one puts a file beside the artifacts that looks
    # like their map and decodes a different build's names -- worse than none.
    if maps[0].stat().st_mtime < since - 5:
        stale = artifact_path(cfg, target).with_suffix(".gd.map")
        fail(f"GDMaim wrote no source map for THIS export -- the newest is "
             f"{maps[0].name}, from before it started. Obfuscation did not run: "
             "check that the plugin is enabled in project.godot and that "
             "script_export_mode is 0.")
        if stale.is_file():
            fail(f"{stale.name} beside the build is from an earlier export and does "
                 "not describe this one -- delete it before handing anything over")
        return
    out = artifact_path(cfg, target)
    destination = out.with_suffix(".gd.map")
    shutil.copy2(maps[0], destination)
    ok(f"source map -> {destination.name} beside the build (never inside an archive)")


def init(cfg: config_lib.Config) -> int:
    """Set a project up: a config read off itself, a local file for this machine,
    two lines in .gitignore and the hook. Writes nothing that already exists."""
    root = cfg.root
    text = (root / "project.godot").read_text(encoding="utf-8")

    features = re.search(r'^config/features=PackedStringArray\("([^"]+)"', text, flags=re.M)
    version = features.group(1) if features else ""

    have_tracked, have_local = config_lib.which_files(root)
    if have_tracked:
        print(f"  {have_tracked} exists, left alone")
    else:
        seen, hygiene, required = _propose_audit(root)
        compute = review_lib.compute_shaders(root)
        starter = _starter(version, seen, hygiene, required, compute)
        (root / config_lib.TRACKED).write_text(starter, encoding="utf-8")
        # Read it back with the fallback parser, whatever this machine has
        # installed. A config only PyYAML can read is a config that works here
        # and breaks every command on the next checkout.
        try:
            yamlish.loads(starter, prefer_pyyaml=False)
        except ValueError as why:
            fail(f"the {config_lib.TRACKED} just written cannot be read by lib/yamlish "
                 f"({why}) -- that is a bug in ship.py's own template, not in your project")
        print(f"  {config_lib.TRACKED}  <- {len(seen)} thing(s) found in this tree, "
              f"{len(required)} the build cannot boot without")
        if compute:
            print(f"  note: {len(compute)} compute shader(s) -- they will not run in a "
                  "web build, the rest of the game will")

    if have_local:
        print(f"  {have_local} exists, left alone")
    else:
        candidates = _engine_candidates(version, root)
        (root / config_lib.LOCAL).write_text(_starter_local(candidates), encoding="utf-8")
        if candidates:
            path, why = candidates[0]
            print(f"  {config_lib.LOCAL}  <- {path.as_posix()}  ({why})")
            for other, other_why in candidates[1:4]:
                print(f"       also matching {version or 'any version'}: "
                      f"{other.as_posix()}  ({other_why})")
        else:
            print(f"  {config_lib.LOCAL}  <- engine: FILL THIS IN, nothing matched "
                  f"{version or 'any version'}")

    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    # `/build/` and `build/` are the same rule to git and different strings to a
    # naive check, which is how a project ends up ignoring its build twice.
    have = {line.strip().strip("/") for line in lines if line.strip()}
    added = [entry for entry in (config_lib.LOCAL, "build/", ".keys/")
             if entry.strip("/") not in have]
    if added:
        ignore.write_text("\n".join(lines + added) + "\n", encoding="utf-8")
        print(f"  .gitignore += {', '.join(added)}")

    # No git hook is installed, here or anywhere else. Writing core.hooksPath
    # into somebody's repository disables every hook it already had, and a build
    # skill has no business touching another project's git configuration at all.
    # `ship.py check-paths` does the same check on demand, when asked.
    _questions(root, version, compute if not have_tracked else [])
    return 1 if failures else 0


def _questions(root: Path, version: str, compute: list[str]) -> None:
    """What init cannot answer, printed as questions with this project's facts in
    them.

    init is half a setup. The other half is six answers that live in somebody's
    head, and an agent that reads "next: ship build" will go and build instead of
    asking -- so the tool says what to ask, and says it last.
    """
    text = (root / "project.godot").read_text(encoding="utf-8") if (root / "project.godot").is_file() else ""
    autoloads = [(n, p) for n, p in review_lib.autoloads(root)]
    dev_autoloads = [f"{n} -> {p.removeprefix('res://')}" for n, p in autoloads
                     if p.removeprefix("res://").startswith("addons/")]
    plugins = re.findall(r'res://addons/([^/"]+)/plugin\.cfg', text)
    beside = []
    engine = config_lib.load(root).engine
    if engine:
        folder = Path(engine).parent
        beside = sorted(p.name for p in folder.glob("*.dll"))[:6]

    step("Ask the user -- one AskUserQuestion call, before anything else")
    web = "windows / macos / web"
    if compute:
        web += f"   (web: {len(compute)} compute shader(s) will NOT run there)"
    print(f"   1. targets      {web}")
    print( "   2. encryption   is the pack encrypted? -> ship.py key, then ship.py templates")
    print(f"      {'':13} never for web: the key ships inside the .wasm")
    print( "   3. clean run    this project's own QA command, the strings that must not")
    print(f"      {'':13} appear, and one that MUST -- a build rendering nothing exits 0")
    print( "   4. obfuscation  -> ship.py obfuscate installs and configures GDMaim")
    print( "   5. payload      anything that must travel beside the exe and is not in git")
    if beside:
        print(f"      {'':13} beside the engine: {', '.join(beside)}"
              + (" ..." if len(beside) == 6 else ""))
    print( "   6. dev addon    excluded by file is not enough -- its autoload stays named")
    if dev_autoloads:
        print(f"      {'':13} autoloads from addons: {'; '.join(dev_autoloads)}")
    if plugins:
        print(f"      {'':13} editor plugins enabled: {', '.join(sorted(set(plugins)))}")
    if not dev_autoloads and not plugins:
        print(f"      {'':13} none found in project.godot")
    print("\n   then:  ship review   ->   ship build")
    return 0


def _propose_audit(root: Path) -> tuple[list[str], list[str], list]:
    """(seen here, plain hygiene, required) -- read off the tree, not copied.

    A generic list is the one thing that cannot be right: what must never ship is
    whatever THIS project happens to carry beside the game. So the tree is walked
    and the two groups are kept apart in the file, because "we saw this in your
    project" and "nobody should ever ship this" deserve different scrutiny.
    """
    seen: list[str] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if entry.is_dir():
            if rules.DEV_DIR.fullmatch(name):
                seen.append(f"{name}/*")
            elif name.startswith(".") and name not in rules.NEVER_FLAG:
                if any(child.suffix.lower() in rules.RESOURCE_EXT
                       for child in entry.rglob("*") if child.is_file()):
                    seen.append(f"{name}/*")
        elif name.startswith(".") and entry.suffix.lower() in rules.RESOURCE_EXT:
            seen.append(name)
    for addon in review_lib.editor_addons(root):
        seen.append(f"{addon}/*")
    for pattern, glob in rules.SIDECARS.items():
        if next(root.glob(glob), None):
            seen.append(pattern)

    hygiene = [p for p in rules.HYGIENE if p not in seen]

    required = [[["project.binary"], 1]]
    text = (root / "project.godot").read_text(encoding="utf-8")
    scene = re.search(r'^run/main_scene="res://([^"]+)"', text, flags=re.M)
    if scene:
        required.append([[scene.group(1), f".godot/exported/*-{Path(scene.group(1)).stem}.scn"], 1])
    translations = list(root.glob("**/*.translation"))
    if translations:
        required.append([[f"{translations[0].parent.relative_to(root).as_posix()}/*.translation"],
                         len(translations)])
    if (root / "default_bus_layout.tres").is_file():
        required.append([["default_bus_layout.tres.remap", "default_bus_layout.tres"], 1])
    return seen, hygiene, required


def _starter(version: str, seen: list[str], hygiene: list[str], required: list,
             compute: list[str]) -> str:
    """The tracked config, written as a file a person will edit -- every list
    carries the sentence that says what it is for. That is why this is YAML and
    not JSON: the explanation has nowhere to live in JSON."""
    lines = [
        "# godot-ship: what a build of this project contains and how it is judged.",
        "# Tracked in git. Machine paths belong in .godot-ship.local.yaml, never here.",
        "",
    ]
    if version:
        lines += [f'engine_version: "{version}"   # refuse to build with any other engine', ""]
    lines += [
        "targets: [windows]        # windows, macos, web",
        "",
    ]
    if compute:
        lines += [
            f"# Before adding web: this project has {len(compute)} compute shader(s) "
            f"({', '.join(compute[:2])}).",
            "# The web export runs on the compatibility renderer, which has no compute",
            "# stage -- the export succeeds and the page loads, and those shaders do",
            "# nothing there. Fine if the rest of the game is the point; not fine if",
            "# they are.",
            "",
        ]
    lines += [
        "encrypt:",
        "  windows: false          # true needs a template built with the key: ship.py templates windows",
        "  # web: never encrypt -- the key would ship inside the .wasm the browser downloads",
        "",
        "obfuscate: false          # true installs and drives addons/gdmaim: ship.py obfuscate",
        "",
        "# A file appearing on disk is not a build. Run the artifact the way this",
        "# project's own rules define a clean run, and read what it says.",
        "boot:",
        "  target: windows",
        "  # Two tokens, never --quit-after=N: Godot reads the count as a separate",
        "  # argument, and the `=` form is passed through to the game, which does not",
        "  # parse it -- the run then never ends and the build hangs until it times out.",
        '  args: ["--headless", "--quit-after", "240"]',
        "  # Name the failures, do not reject every ERROR:. Headless has no display",
        "  # server, so a healthy build still prints several of those -- and the one",
        "  # line that matters most is in the list below by name.",
        "  #",
        "  # Written as a block list rather than a wrapped [\"a\", \"b\"] one: lib/yamlish.py",
        "  # reads a flow sequence only while it fits on one line, and this file has to",
        "  # parse on a machine with no PyYAML -- which is most of them.",
        "  reject:",
        '    - "SCRIPT ERROR"',
        '    - "Parse Error"',
        '    - "push_error"',
        '    - "Failed to load script"',
        '    - "Can\'t open encrypted pack"',
        '    - "Couldn\'t load project data"',
        "  expect: []              # lines that MUST appear; a build that renders nothing still exits 0",
        "  min_pack_kb: 100        # an encrypting export with no key writes a ~100-byte pack and calls it success",
        "",
        "audit:",
        "  # What may never reach a player. Keep this in step with the preset's",
        "  # exclude_filter -- the two drifting apart is how a dev config file with a",
        "  # whole toolchain in it shipped in a release while --check said 'nothing forbidden'.",
        "  forbidden:",
    ]
    if seen:
        lines.append("    # Found in THIS project. Read them: the list is a starting point and")
        lines.append("    # an editor addon or a tools folder may be something the game loads.")
        lines += [f'    - "{pattern}"' for pattern in seen]
    if hygiene:
        lines.append("    # Nothing should ship these, whether or not this project has any today.")
        lines += [f'    - "{pattern}"' for pattern in hygiene]
    lines += [
        "  # What the game does not boot without. Alternatives in one entry count together:",
        "  # a scene ships as .tscn or as .godot/exported/*.scn depending on the export.",
        "  required:",
    ]
    for patterns, count in required:
        listed = ", ".join(f'"{p}"' for p in patterns)
        lines += [f"    - paths: [{listed}]", f"      count: {count}"]
    lines += [
        "",
        "paths:",
        '  skip: ["*.md"]          # documentation is where a machine path belongs',
        "  allow: []               # regexes for a path that is genuinely part of the project",
        "",
        "# The rest of the keys, commented because most projects need none of them.",
        "# They are here rather than in a manual: a key you cannot see is a key you go",
        "# looking for in the source.",
        "#",
        "# Runtime libraries the exporter does not carry and git does not hold. Without",
        "# them the build starts and quietly renders on whatever the player's system",
        "# provides -- a downgrade nobody files a bug for.",
        "# payload:",
        '#   from_engine_dir: ["sl.*.dll", "nvngx_*.dll", "D3D12Core.dll"]',
        '#   from_project: ["addons/godotsteam/win64/steam_api64.dll"]',
        "#   expect_count: 12      # fewer than this fails the build",
        "#",
        "# One .zip beside the artifacts: the exe, the pack and every .dll BY NAME.",
        "# Never the folder -- an older build or a leftover .dev rides along unnoticed.",
        "# archive: false",
        "#",
        "# Taken out of project.godot for the length of the export and put back. Both",
        "# halves or neither: excluding a dev addon's files leaves its autoload named",
        "# in project.godot, pointing at nothing, and the first frame is ERROR lines.",
        "# strip:",
        '#   autoloads: ["MCPRuntimeProbe"]',
        '#   plugins: ["res://addons/godot_mcp/plugin.cfg"]',
        "#",
        "# Where to look for symbols reached by string, when obfuscating. Default: all.",
        '# obfuscation: {scan: ["src", "addons/weather"]}',
        "#",
        "# Where the pack key lives, if not .keys/dev.gdkey.",
        '# key: ".keys/dev.gdkey"',
        "",
    ]
    return "\n".join(lines)


def _starter_local(candidates: list[tuple[Path, str]]) -> str:
    """Paths are written with forward slashes.

    A Windows path in a double-quoted YAML scalar is not a path, it is a string
    full of escape sequences -- a drive letter followed by backslashes fails to
    parse outright, and init wrote a config its own loader could not read. Godot
    takes forward slashes on every platform.
    """
    lines = [
        "# Where things live on THIS machine. Git-ignored, and nothing here is ever",
        "# written into a tracked file: ship.py puts the template path into the preset",
        "# for the length of the export and takes it back out afterwards.",
        "",
    ]
    if candidates:
        path, why = candidates[0]
        lines.append(f'engine: "{path.as_posix()}"   # {why}')
        if len(candidates) > 1:
            lines.append("# Other builds of the same version were found. Two builds of one")
            lines.append("# version are indistinguishable by --version -- a stock engine and a")
            lines.append("# fork of it differ only in which one this project is built with:")
            for other, other_why in candidates[1:4]:
                lines.append(f'#   engine: "{other.as_posix()}"   # {other_why}')
    else:
        lines.append('engine: ""   # <- the exact binary this project is built with')
    lines += [
        "",
        "template:",
        '  # windows: "<full path to>/windows_release_x86_64.exe"',
        "  # Needed only when this target encrypts: an official template carries an",
        "  # EMPTY key and the build cannot read its own pack.",
        "",
        '# gdmaim_src: "<full path to a gdmaim checkout>"   # cloned from upstream when absent',
        "",
    ]
    return "\n".join(lines)


# A path to a Godot binary, as it appears in a launcher script or a document.
ENGINE_IN_TEXT = [
    re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*godot[^\s\"'<>|]*\.exe", re.I),
    re.compile(r"/(?:[^\s\"'<>|/]+/)*godot[^\s\"'<>|]*", re.I),
]
# Where a project says which engine it is built with, in practice: its own
# launcher scripts, its instructions, its editor and tool configuration.
ENGINE_HINT_FILES = {".bat", ".cmd", ".ps1", ".sh", ".md", ".json", ".cfg", ".txt"}
ENGINE_HINT_SKIP = {".godot", ".git", "build", "addons", ".claude", "node_modules"}


def _engine_in_project(root: Path) -> list[tuple[Path, str]]:
    """Engines the project itself names, most-mentioned first.

    This beats searching the disk, and by a lot: two builds of the same version
    are indistinguishable by `--version` -- a stock 4.8 and a fork of 4.8 differ
    only in which one the project is actually built with. The project says so in
    its own launcher and its own instructions; nothing else knows.
    """
    counts: dict[str, int] = {}
    where: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in ENGINE_HINT_FILES:
            continue
        if ENGINE_HINT_SKIP & set(path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in ENGINE_IN_TEXT:
            for match in pattern.finditer(text):
                hit = match.group(0).replace("\\\\", "\\")
                if not Path(hit).is_file():
                    continue
                key = str(Path(hit).resolve())
                counts[key] = counts.get(key, 0) + 1
                where.setdefault(key, path.relative_to(root).as_posix())
    order = sorted(counts, key=lambda k: -counts[k])
    return [(Path(k), f"named in {where[k]}") for k in order]


def _engine_candidates(want: str, root: Path) -> list[tuple[Path, str]]:
    """Every engine worth offering, best first, each with why it is here.

    The project's own word comes first. Only when it says nothing does this fall
    back to looking around -- and where to look is derived, never spelled out:
    naming somebody's install directory in shared code is the mistake this skill
    refuses to commit.
    """
    found: list[tuple[Path, str]] = _engine_in_project(root)

    folders: list[Path] = []
    for entry in (os.environ.get("GODOT_SEARCH") or "").split(os.pathsep):
        if entry.strip():
            folders.append(Path(entry.strip()))
    folders += [root.parent, Path.home()]
    on_path = shutil.which("godot")
    if on_path:
        found.append((Path(on_path), "on PATH"))
    for folder in folders:
        if folder.is_dir():
            for candidate in sorted((p for p in folder.glob("godot*") if p.is_file()),
                                    key=lambda p: p.stat().st_mtime, reverse=True):
                found.append((candidate, f"found in {folder.as_posix()}"))

    seen: set[str] = set()
    out: list[tuple[Path, str]] = []
    for path, why in found:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        # A candidate scraped out of the project's own text files, pointing at a
        # binary inside the project, is the project nominating what runs here --
        # and the next line would run it. Never offered, never executed.
        if not config_lib.outside_project(root, str(path)):
            warn(f"ignoring {path} -- named by this project and living inside it")
            continue
        # Version is checked rather than assumed: taking the newest godot* is how
        # a 4.7 project ends up pointed at a 4.8 fork that cannot use its
        # template and fails without saying why.
        if want and not config_lib._version_of(str(path)).startswith(want):
            continue
        out.append((path, why))
    return out


def run_audit(cfg: config_lib.Config, target: str, everything: bool, checking: bool) -> int:
    out = artifact_path(cfg, target)
    pack = out.parent / "index.pck" if target == "web" else out.with_suffix(".pck")
    source = pack if pack.is_file() else out
    if not source.is_file():
        raise SystemExit(f"nothing to audit at {source} -- build it first")
    data, base = audit_lib.find_pack(source)
    (version, major, minor, patch), entries = audit_lib.read_pack(data, base)
    total = sum(size for _, size in entries)
    print(f"pack v{version}, godot {major}.{minor}.{patch}")
    print(f"{len(entries)} files, {total / 1048576:.1f} MB\n")
    if checking:
        rules = cfg["audit"]
        return audit_lib.check(entries, rules["forbidden"],
                               [(tuple(p), n) for p, n in rules["required"]])
    audit_lib.summarise(entries)
    if everything:
        print("\n=== every file, largest first ===")
        for path, size in sorted(entries, key=lambda e: -e[1]):
            print(f"{size:12}  {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="project root (default: search upwards)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="export, verify and boot")
    p.add_argument("targets", nargs="*")
    p.add_argument("--debug", action="store_true")

    sub.add_parser("doctor", help="report state, change nothing")

    p = sub.add_parser("serve", help="serve the web export over HTTP")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--dir")

    p = sub.add_parser("audit", help="what is actually inside the pack")
    p.add_argument("target", nargs="?", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--check", action="store_true")

    sub.add_parser("boot", help="run the exported artifact and read its output")

    p = sub.add_parser("check-paths", help="absolute machine paths about to be committed")
    p.add_argument("--tree", action="store_true", help="scan everything tracked, not the index")

    sub.add_parser("init", help="set this project up: configs, .gitignore, hook")

    sub.add_parser("review", help="audit the setup: what is not covered, and what leaks")

    p = sub.add_parser("key", help="create the pack encryption key, once")
    p.add_argument("--force", action="store_true", help="replace an existing key (orphans old builds)")

    p = sub.add_parser("obfuscate", help="install and configure GDMaim, keep its lock list")
    p.add_argument("--locks", action="store_true", help="regenerate the lock list")
    p.add_argument("--check", action="store_true", help="report only, change nothing")

    p = sub.add_parser("templates", help="build export templates with the key inside")
    p.add_argument("platform")
    p.add_argument("--src", help="Godot source tree")

    args = parser.parse_args()
    root = Path(args.project).resolve() if args.project else config_lib.find_project(Path.cwd())
    cfg = config_lib.load(root)

    if args.command == "doctor":
        return doctor(cfg)
    if args.command == "build":
        build(cfg, args.targets or cfg.targets, args.debug)
        return 0
    if args.command == "boot":
        boot(cfg)
        verdict()
        return 0
    if args.command == "serve":
        folder = Path(args.dir) if args.dir else artifact_path(cfg, "web").parent
        return serve_lib.serve(folder, args.port)
    if args.command == "audit":
        target = args.target or ("web" if "web" in cfg.targets else cfg.targets[0])
        return run_audit(cfg, target, args.all, args.check)
    if args.command == "check-paths":
        return check_paths(cfg, staged=not args.tree)
    if args.command == "init":
        return init(cfg)
    if args.command == "review":
        return review(cfg)
    if args.command == "key":
        return make_key(cfg, force=args.force)
    if args.command == "obfuscate":
        return obfuscate(cfg, regenerate=args.locks, check=args.check)
    if args.command == "templates":
        cmd = [sys.executable, str(HERE / "lib" / "build_templates.py")]
        if args.src:
            cmd += ["--src", args.src]
        cmd.append(args.platform)
        return subprocess.run(cmd, cwd=root).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
