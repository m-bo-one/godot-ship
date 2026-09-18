# CLAUDE.md

**godot-ship** — export a Godot project for Windows, macOS or the browser, prove the export is
worth handing over, and keep machine paths out of the repository.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A Claude Code **skill** that is also a standalone Python CLI. It is not a Godot project and holds
no project- or machine-specific data — [SKILL.md](SKILL.md) is the agent-facing entry point (its
frontmatter drives skill discovery), [ship.py](ship.py) the command line.

It lives at `~/.claude/skills/godot-ship` and is never copied into a game project. Anything that
would name one project or one computer belongs in the config files `ship.py init` writes into the
*target* project, not here.

## Running it

There is no build step and no dependency install. Standard library only, Python 3.9+; PyYAML is
used when it happens to be installed and never required ([lib/yamlish.py](lib/yamlish.py) is why).
The unit tests need no Godot and no project -- they fake the exporter subprocess:

```
py -m unittest discover -s tests
```

Exercise changes against a real Godot project, from anywhere:

```
py ship.py --project <godot project> doctor        # read-only, start here
py ship.py --project <godot project> review        # read-only, the full setup audit
py ship.py --project <godot project> audit --check # read-only, reads an existing pack
py ship.py --project <godot project> check-paths --tree
py ship.py --project <godot project> build [windows|macos|web]
```

Every command takes `--project`; without it the CWD is walked upwards for `project.godot`.
`review` and `check-paths` work outside a Godot project too. `init`, `key`, `obfuscate`,
`playgama`, `templates` and `build` **write into the target project** — `doctor`, `review`,
`audit` and `check-paths` never do.

## Architecture

[ship.py](ship.py) is the CLI, the orchestration, and everything that writes into a user's
project. `lib/` is analysis and pure helpers:

| Module | Holds |
|---|---|
| [lib/config.py](lib/config.py) | the two-file config, `DEFAULTS`, engine/template/key resolution |
| [lib/rules.py](lib/rules.py) | **every heuristic table** — path regexes, leak patterns, dev-dir names, sidecars |
| [lib/audit.py](lib/audit.py) | the GDPC pack format (v1–v4), read directly out of the `.pck` or an embedded exe |
| [lib/review.py](lib/review.py) | read-only judgement: uncovered entries, editor addons, autoloads, what an artifact leaks |
| [lib/paths.py](lib/paths.py) | the machine-path scanner; reads the git **index**, not the working tree |
| [lib/gdmaim.py](lib/gdmaim.py) | GDMaim install, its settled settings, the string-reached-symbol lock scan |
| [lib/playgama.py](lib/playgama.py) | Playgama Bridge install, its autoload/plugin registration, the two-preset split |
| [lib/build.py](lib/build.py), [lib/build_templates.py](lib/build_templates.py) | the exporter and the scons template build |
| [lib/yamlish.py](lib/yamlish.py) | the YAML subset the configs are written in |

`lib/build.py` and `lib/build_templates.py` are launched as **subprocesses** (`sys.executable`),
not imported — they stay runnable on their own, and ship.py hands them state through the
environment (`$GODOT`, `$GODOT_SCRIPT_ENCRYPTION_KEY`) and `--project`. Keep that boundary.

Reporting is a module-level `failures` list plus `step`/`ok`/`warn`/`fail`, and `verdict()` turns
it into an exit code. A new check calls `fail()` and lets the verdict decide; it does not raise.

## Invariants that carry the design

1. **No absolute machine path ever reaches a tracked file.** `patch_presets()` and
   `strip_project()` write the machine's template path and drop dev autoloads *for the length of
   the export*, and [ship.py:311](ship.py#L311) restores both in a `finally`. Any new
   export-time mutation goes in that same block — a crash between patch and restore leaves a
   poisoned `export_presets.cfg` in someone's repo.
2. **Two configs, and the split is the point.** `godot-ship.yaml` is tracked and says what a build
   contains; `.godot-ship.local.yaml` is git-ignored and says where things live on this machine.
   Adding a config key means: a default in `DEFAULTS` ([lib/config.py](lib/config.py)) *and* a
   line — commented, if most projects do not need it — in `_starter()` ([ship.py:861](ship.py#L861))
   carrying the sentence that says what it decides. SKILL.md deliberately keeps no copy of the
   schema; the generated file is the documentation.
3. **`yamlish` must be able to read what `_starter()` writes.** No anchors, no multi-line scalars,
   no flow maps, no flow sequence wrapped across lines, and forward slashes in quoted paths — a
   Windows path in a double-quoted scalar is a string of escape sequences. `init` now checks this
   itself, parsing what it just wrote with `yamlish.loads(..., prefer_pyyaml=False)`: PyYAML on the
   machine that writes a config hides anything the fallback parser cannot read, and the next
   checkout meets it as every command failing at once.
4. **Engine precedence is deliberate**: `$GODOT_SHIP_ENGINE` > local config > `$GODOT` /
   `$GODOT_EXE` / `$GODOT_BIN`. The generic variables come last because they tend to be set
   machine-wide to whatever engine was installed most recently, and a project pinned to one
   version must not be hijacked by that.
5. **[.githooks/pre-commit](.githooks/pre-commit) is this repository's own hook** (`core.hooksPath=.githooks`).
   No command installs a hook into a user's project and none should: `core.hooksPath` overrides
   every hook a repo already has. Elsewhere the same check runs on demand as `check-paths`.
6. **New heuristics go in [lib/rules.py](lib/rules.py)**, not in the module that happened to need
   them. The code that matches is stable; the lists are what gets edited. `rules.HYGIENE` *is*
   `DEFAULTS["audit"]["forbidden"]` — imported, never copied. The two were written out separately
   once and drifted, and since a project's own list replaces the default rather than extending it,
   the shorter copy silently shrank the net for every configured project.
7. **Everything read out of the target project is untrusted input**, including
   `.godot-ship.local.yaml` — the name says local, but nothing stops a repository shipping one.
   Two rules follow. A repo-resident file may not name a binary that is *also* inside the repo
   (`config.outside_project`); only `$GODOT_SHIP_ENGINE`, which cannot arrive in a clone, may name
   anything. And a path read from the project — `export_path`, `key:` — is contained before it is
   used, because `root / "<absolute>"` discards the root. `doctor`, `review`, `audit` and
   `check-paths` must execute nothing and write nothing.
8. **A check that could not run is never reported as a pass.** [lib/paths.py](lib/paths.py) raises
   `GitUnavailable` instead of treating a failed `git` as an empty file list, and `scan()` returns
   the files it could not read alongside the findings. An empty result and a broken check look
   identical otherwise, which is how the whole thing became an unconditional pass outside a
   repository.
9. **The preset generator writes only what an export fails without** (`PRESET_OPTIONS` in
   [lib/build.py](lib/build.py)). Everything else stays in the project's own
   `export_presets.cfg`, by hand. A *variant's* preset is never generated at all.
10. **A target is a `Variant`** (`Config.variant()` in [lib/config.py](lib/config.py)): a platform
    plus a preset name, output, strip list and archive. `ship.py` never asks "is target == web";
    it asks `variant.platform`. Presets are found **by name** (`_preset_span`); only a bare
    platform target falls back to "the first preset of that platform", for projects from before
    variants. Each variant is one `lib/build.py` process with `--preset`/`--out`, and
    `project.godot` is stripped and restored around *each* one -- a variant's `strip` replaces
    the top-level one, never merges with it.

## House style

Docstrings and comments state **the failure the rule prevents**, in prose — nearly every table
entry and settings value here has a broken build behind it, and a rule with no rationale reads as
arbitrary and gets deleted by the next person. Match that density rather than trimming to it.

Windows-first and cross-platform: streams are reconfigured to UTF-8 at import (a console defaults
to cp1252 and dies on non-Latin output), `os.system("")` turns on ANSI in a legacy console, paths
are written with forward slashes.

Note: a few docstrings still say `godot-ship.json` from before the move to YAML. The tracked file
is `godot-ship.yaml`; `.yml` and `.json` are still read when a project has one.

## Keeping the three documents in step

A new command or changed behaviour touches four places: `ship.py`'s module docstring, its
`argparse` help, the command tables in [SKILL.md](SKILL.md), and the tables in
[README.md](README.md). SKILL.md is written for an agent (it carries the judgement — the nine-step
audit, the failure modes); README.md is written for a person.
