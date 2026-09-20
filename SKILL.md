---
name: godot-ship
description: Export this Godot project for Windows, macOS or the browser, prove the export is worth handing over, and keep machine paths out of the repository. Use when asked to build, export, package, ship or publish the game, to make a web/HTML5 or itch build, to encrypt or obfuscate a build, to check what is inside a pack, or when an exported build misbehaves — a pack it cannot read, "Failed to fetch" in a browser, an exe Windows refuses to start.
---

# godot-ship

One command exports every target this project declares and checks what came out:

```
py ~/.claude/skills/godot-ship/ship.py build
```

| Need | Command |
|---|---|
| Everything, checked | `ship.py build` |
| One target | `ship.py build web` |
| One named variant of a platform | `ship.py build playgama` — see **Variants** |
| Playgama Bridge into the project | `ship.py playgama` — addon, autoload, plugin, its own preset |
| The GamePix plugin into the project | `ship.py gamepix` — the same, and its preset MUST be named `GamePix…` |
| The CrazyGames SDK into the project | `ship.py crazygames` — needs `crazygames_src`: that addon has no working download |
| Audit the setup, not just the pack | `ship.py review` |
| What is installed and configured | `ship.py doctor` |
| Open a web build | `ship.py serve [target]` — it cannot run from disk; `--plain` for a store's QA tool |
| What is actually inside the pack | `ship.py audit --check` |
| Run the artifact and read its output | `ship.py boot` |
| Machine paths about to be committed | `ship.py check-paths` |
| Create the pack key, once | `ship.py key` |
| An export template with the key inside | `ship.py templates windows --src <godot source>` |

## Three ways this gets asked for

Only the first one is a setup; the other two are the working loop.

### `/godot-ship set this project up for shipping`

**`ship.py init` is half of this, and stopping there leaves the project
misconfigured.** It reads what it can off the tree, writes both config files, and
ends by printing an `Ask the user` block with this project's own facts already in
it — the dev autoload it found, the libraries beside the engine, whether a web
build would lose compute shaders.

**Put those six to the user with `AskUserQuestion`, in one call, before running
anything else.** Not as prose in a message: the answers are a fixed set and the
user should be picking from it. They cannot be read off `project.godot`, and
guessing any of them wrong produces a build that looks fine and is not.

1. **Which targets?** `windows`, `macos`, `web` — multi-select.
   When the project has compute shaders (`.glsl`, `.slang`), **still offer `web`,
   and say in the option what it costs**: the web export runs on the
   compatibility renderer, which has no compute stage, so those shaders do
   nothing there while the rest of the game works. Fine if they are decoration,
   not fine if they are the game. `init` finds them and writes the reason into
   the config; `review` warns every time rather than refusing, because the
   failure is silent — the export succeeds and the page loads.
2. **Is the pack encrypted?** Ask it plainly — it is the one answer that costs
   something later, because it needs a custom export template compiled around the
   key. Never for `web`: the key would ship inside the `.wasm`.
   If yes: `ship.py key` writes `.keys/dev.gdkey` and git-ignores it, then
   `ship.py templates <target> --src <godot source>` builds the template around
   it. An existing key is kept, never silently replaced — a new one orphans every
   build already shipped. Say out loud that the file must be backed up outside
   the repository.
3. **How does this project define a clean run?** Take the arguments from its own
   QA commands, the strings that must not appear in the output, and — this one is
   usually forgotten — a line that *must* appear, because a build that renders
   nothing still exits 0.
4. **Obfuscated?** If yes, `ship.py obfuscate` installs and configures GDMaim.
5. **Does anything have to travel beside the executable?** Runtime libraries the
   exporter does not carry and git does not hold — a vendor SDK, a Steam library,
   whatever sits next to the engine. Without them the build starts and silently
   renders on whatever the player's system provides, which nobody reports as a
   bug. They go in `payload`, sourced from the engine directory or the project.
6. **Is there a dev addon inside?** An editor bridge, a debug probe, a profiler.
   Excluding its files is not enough: its autoload stays named in `project.godot`
   and the player's first frame is ERROR lines. Both halves go in `strip`.

Ask 5 and 6 by looking, not by asking blind: read `project.godot`'s `[autoload]`
and `[editor_plugins]` sections and the directory the engine binary sits in.

Then fill `audit.forbidden` by walking the tree, not by copying a list: dev
addons, tool folders, keys, editor-only scenes, sidecar files that record how an
asset was made. Finish with `ship.py review` and hand over what it says.

### `/godot-ship audit the build` — is anything leaking, is anything uncovered

`ship.py review` is the mechanical half. The judgement is below, under
**The audit**, and it is the half that finds things.

### `/godot-ship build the release` — build it

`ship.py build`. Report the artifact sizes, the boot result, and what is still
owed by hand. If the pack is encrypted, say plainly that the file-by-file audit
was skipped and offer an unencrypted export to run it against.

## Two config files, and the split is the whole design

| File | In git | Holds |
|---|---|---|
| `godot-ship.yaml` | **yes** | what a build contains and how it is judged: targets, encryption, boot check, audit lists |
| `.godot-ship.local.yaml` | **no** | where the engine, the templates and the key live *on this machine* |

`ship.py init` writes both, reading what it can off `project.godot`. YAML and not
JSON because every list in there wants a sentence saying what it is for, and JSON
has nowhere to put one. `.yml` and `.json` are read too when a project has them.

Nothing machine-specific is ever written into a tracked file. That is not tidiness:
`custom_template/release="C:/Users/someone/…"` in `export_presets.cfg` works on
exactly one computer, breaks every other checkout, and publishes a user name.
The tracked preset keeps that field empty; `ship.py` writes the real path in for
the length of the export and puts it back in a `finally`.

`GODOT_SHIP_ENGINE`, `GODOT_TEMPLATE_<TARGET>` (a variant's name, then its
platform) and `GODOT_SCRIPT_ENCRYPTION_KEY` override the local file, so CI needs no file at all. Note the order: the local
config beats a bare `GODOT` or `GODOT_EXE`, which tend to be set machine-wide to
whatever engine was installed last — a project pinned to one version must not be
hijacked by that.

**The keys are in the file, not here.** `ship.py init` writes both configs with a
comment on every key, and the ones most projects do not need — `payload`,
`archive`, `strip`, `variants`, `obfuscation.scan`, `key` — are written out commented, with
what each decides. Read `godot-ship.yaml`; a second copy of the schema in this
document would be a second copy to keep in step, and it would lose.

**What the preset generator does NOT set.** A preset appended for a missing
platform carries only what an export fails without: architecture, texture
formats, `embed_pck=false`, and the web settings. Everything else the project
needs goes into `export_presets.cfg` by hand and stays there —
`application/export_d3d12`, `application/modify_resources`, the icon, the file
description. During an export ship.py touches exactly two things and puts both
back: `custom_template/release` and the encryption flags.

## Variants: two builds of one platform from one tree

A store build that carries an SDK and a store build that must not — two web
exports, one `project.godot`. `variants:` in `godot-ship.yaml` names them:

```yaml
targets: [windows, web, playgama]
variants:
  playgama:
    platform: web                 # which platform it is an export of
    preset: "Web Playgama"        # its own block in export_presets.cfg, by hand
    out: build/playgama/web/index.html
    archive: build/playgama/game-web.zip   # flat, index.html at the zip root
    addon: playgama_bridge        # installed before the export when absent
    post: python tools/pack_web.py  # the project's own last step, see below
    strip:                        # REPLACES the top-level strip for this export
      autoloads: ["QaDriver"]
  web:                            # the plain build: the SDK held out of it
    strip:
      autoloads: ["QaDriver", "Bridge"]
      plugins: ["res://addons/playgama_bridge/plugin.cfg"]
```

A target with no entry is the platform itself and builds exactly as before.
What a variant changes, and why each rule is the way it is:

- **One export per variant, `project.godot` rewritten around each.** A single
  strip for the whole build would have to be the union of the lists, which
  drops from every store's build what only one of them must not carry. Each
  export strips its own list and puts the file back before the next.
- **A variant's `strip` replaces, never merges.** Merging would make it
  impossible for one variant to keep an autoload the others drop — the whole
  reason the variant has a list.
- **Its preset is found by name, never by platform.** With two Web presets in
  the file, "the first Web one" is the other store's shell half the time. A
  missing variant preset is an error, not a block to generate: it carries what
  no generator knows — the store's HTML shell, its own exclude list.
- **Both halves or neither, per variant.** `review` checks each variant's
  strip list against its own preset's `exclude_filter`: an autoload kept while
  its script is excluded fails (ERROR lines on the first frame); one dropped
  while its files ship, or a plugin dropped while its autoload stays, warns —
  the plugin re-registers the autoload the next time the editor loads.
- **Template, key and `encrypt:` are read by variant name, then by platform**,
  in the local file and in `GODOT_TEMPLATE_<NAME>` alike, so
  `template: {web: …}` covers every web variant and one can still be pointed
  at a template of its own.
- **`archive:` on a variant** zips the export flat — `index.html` at the root
  of the zip, Latin names only, `.import` sidecars and files an *earlier*
  export left in the folder held out and named. That is what a store uploader
  accepts, and the leftover-file rule exists because a build with the SDK held
  out once shipped the SDK's `.js` from the export before it.
- **`post:` on a variant** is a command run from the project root after the
  export, the file check and the archive; a non-zero exit fails the build, and
  the archive's size is read again afterwards. It exists for the step a store
  forces on a project and the project used to run by hand — gzip the `.wasm`
  and the `.pck` and put a fetch shim in `index.html`, because the store's host
  sends no HTTP compression and counts the bytes in full against its size
  budget. Run by hand, it is the step forgotten before an upload. Only `build`
  runs it; `doctor`, `review` and `audit` execute nothing.
- `audit`, `boot`, `serve` and `review` take a variant name where they take a
  target; `boot` skips a web variant, `serve` defaults to `web` or the first
  web variant. The obfuscation lock scan runs once per build — the tree is
  the same for every variant of it.

**Playgama.** `ship.py playgama` installs `addons/playgama_bridge` (from
`playgama_bridge_src` in the local config — a checkout, the addon folder or
the release zip — else the latest release of `Playgama/bridge-godot-4`, the
Godot 4 line; `bridge-godot` without the suffix is the Godot 3 addon), puts
the `Bridge` autoload *first* in `[autoload]` and the plugin in
`[editor_plugins]`, and splits the Web preset in two: the plain one loses the
Bridge shell and excludes `addons/playgama_bridge/*`, the new `"Web Playgama"`
keeps the shell and the addon. Then it prints the `variants:` block to paste.
Playgama's uploader wants exactly the flat zip above. Bridge is also how
GameDistribution, CrazyGames, Yandex and others are reached — they are
platforms inside the same js, chosen at run time by hostname — so the one
Playgama build serves them; GameDistribution needs only
`platforms.game_distribution.gameId` in `playgama-bridge-config.json`.

**GamePix** is not inside Bridge; it has a Godot plugin of its own, and
`ship.py gamepix` does for it what `ship.py playgama` does for Bridge: the
addon (from `gamepix_src`, else the archive linked from
my.gamepix.com/sdk/doc/godot-plugin — there is no release API, the URL is a
constant), the `GPX` autoload first, the plugin, a `"GamePix"` preset copied
from the plain Web one. Two things about it are not choices:

- **The preset's name must begin with `GamePix`.** The plugin's `_enter_tree`
  — a headless export included — appends a preset of its own whenever no preset
  name does: `all_resources`, an *empty* `exclude_filter`, no
  `custom_template` keys. Name yours "Web GamePix" and the file grows a second,
  unfiltered one called exactly "GamePix" that ships `docs/`, `tools/` and
  every dev addon. `review` warns about any `GamePix*` preset that looks like
  that. The tool writes the preset *before* it enables the plugin, for the
  same reason.
- **The folder is `addons/gpx-godot-plugin`**, whatever the zip is called:
  `plugin.gd` hardcodes the shell path under it.

**CrazyGames** has an official Godot addon too, and `ship.py crazygames` sets
it up — with four differences that are all read off the addon itself:

- **It has no working download -- check before promising one.** The docs link a
  single source, a Godot Asset Store page, and when last checked (2026-09-19)
  that asset was withdrawn: a browser gets "The requested asset is not
  available", and the store's API answers 404 for it while serving every other
  asset. A 403 on that page reads like a bot wall and is not one. So the
  command cannot fetch it and neither, possibly, can the user: the addon comes
  from CrazyGames developer support, or from the `addons/crazygames` folder of
  a project that already has the official one (it holds
  `Utils/CrazyGamesBridge.gd`; a third-party lookalike under the same folder
  name does not, and is refused). `crazygames_src` takes the zip, the unpacked
  archive or that folder. **Offer the Bridge road first** (below) when the
  project already speaks Bridge: it needs no addon.
- **The zip holds two addons**, `crazysdk-godot-3` and `crazysdk-godot-4`; the
  tool takes the second and installs it as `addons/crazygames`, the name the
  plugin's own script hardcodes.
- **Two autoloads, order-dependent**: `CrazyGamesBridge` then `CrazyGames` —
  the second calls the first in its `_ready`. Both go first in `[autoload]`,
  and *both* names go in every other web variant's `strip.autoloads`.
- **No HTML shell and no special preset name.** The SDK's js is appended to
  `<head>` at run time, so the `"CrazyGames"` preset is the plain Web one with
  the rival SDKs excluded, plus `user-select:none` on the body through
  `html/head_include` — the store asks for it on mobile, where a long press
  otherwise selects the whole game. It must export as `index.html`.

After a build of that variant the tool judges the export against the store's
hard limits, which otherwise surface at upload: at most 1500 files and 250 MB
(fail), and an initial download — everything fetched before
`CrazyGames.Game.gameplay_start()` — of at most 50 MB, or 20 MB to be eligible
for the mobile homepage (warnings). A Godot export fetches its `.wasm` and
`.pck` whole before the first frame, so the export's size *is* that number.
What no tool checks: `gameplay_start()`/`gameplay_stop()` are mandatory for a
full launch, only SDK ads, no custom fullscreen button, no cross-promotion,
English present, PEGI 12, and a new player in gameplay within one click.

There is a second road: CrazyGames is also a platform inside Playgama Bridge
(`crazy_games`, by hostname), so a project already speaking Bridge can upload
its Playgama build there and write no second adapter. One or the other.

**Each web variant carries exactly the SDK it names, and no other.** GamePix:
"games must either use the GamePix SDK or be entirely SDK-free" — and the
others are no kinder. Every provider's setup command holds its addon out of
every *other* web preset and holds the other installed SDKs out of its own;
the `strip` lists are yours, and the command prints them. `review` warns per
web variant about another store's autoload still registered, its plugin still
enabled (it re-registers the autoload during the export), or its files not
excluded — and a plain `web` variant, naming no SDK, must carry none. Note
what none of this does: the *game* still has to talk to `GPX` where it talked
to `Bridge`; the tool ships the SDK, it does not write the adapter.

## Machine paths

```
py ~/.claude/skills/godot-ship/ship.py check-paths          # what is staged
py ~/.claude/skills/godot-ship/ship.py check-paths --tree   # everything tracked
```

It reports absolute paths belonging to one computer — a Windows drive path, a UNC
share, `/home/<user>/`, `/Users/<user>/` — in anything about to be committed. By
default it reads the **index**, so a half-staged fix is judged by the half that
ships.

**No git hook is installed into a project, and there is no command that does.**
`core.hooksPath` overrides every hook a repository already has, and a build skill
has no business rewriting somebody's git configuration. godot-ship keeps a hook
for its own commits; anywhere else this runs when asked.

Documentation is where a path belongs, so `*.md` is skipped by default. Anything
else legitimate goes in `paths.allow` in `godot-ship.yaml` as a regex.

## The empty pack

Godot takes the pack key from `GODOT_SCRIPT_ENCRYPTION_KEY`. **An encrypting
preset with that variable unset exports without one error line and writes a
pack of about a hundred bytes.** The finished game then opens with

```
ERROR: Can't open encrypted pack directory.
Error: Couldn't load project data at path "…". Is the .pck file missing?
```

on a player's machine — never on the machine that built it, if that one has the
key sitting in `.godot/export_credentials.cfg` from a pass through the editor.
`ship.py` sets the variable itself and rejects any pack under `boot.min_pack_kb`.

## The key exists twice and nothing compares the halves

The exporter reads it from the environment; the finished game only ever has the
one **compiled into its export template**. Official templates carry an empty key,
so an encrypting preset on an official template exports cleanly and ships a build
that cannot read its own pack. Preflight therefore searches the template binary
for the key's own 32 bytes and refuses to build when they are not there.

Building a template around a key is a scons build of the matching Godot source:

```
py ship.py templates windows --src <godot source checked out at your engine's tag>
```

The source tree must be at the tag matching the editor — a template from a
different build loads and then misbehaves in ways that read like project bugs.

## The web build needs a server standing next to the html

**Opening `index.html` from disk fails with `Failed to fetch`.** Not a broken
export: the page pulls `index.wasm` and `index.pck` with `fetch()`, which a
browser refuses on a `file://` origin, and a file off the disk carries no
`application/wasm` type for the streaming compile either.

```
py ship.py serve          # http://127.0.0.1:8000/, right headers, Ctrl+C stops it
py ship.py serve --plain  # no isolation headers: a store's QA tool loading this
                          # server otherwise sees "AdBlock" and shows no ad
```

Real hosting needs none of this. Three preset settings are load-bearing and two
fail silently:

- `variant/thread_support=false` — threads need `SharedArrayBuffer`, which needs
  `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
  require-corp`; without them a thread-enabled export stops at a black screen;
- `vram_texture_compression/for_mobile=false` — with it on, the export fails with
  a bare "configuration errors" line and no reason;
- `html/canvas_resize_policy=2` — the canvas follows the window.

A web build is effectively public source: encryption is pointless there, because
the key would ship inside the `.wasm` the browser downloads.

## The audit

`ship.py review` runs the mechanical checks: config and hook present, encryption
sane, top-level entries nobody named, editor addons, autoloads pointing into
excluded folders, strings readable in the artifacts, machine paths in the tree.
It cannot judge, and judgement is where the findings are. Walk these nine, in
order, and read the answers rather than the exit code.

**1. Read the pack file by file, not the megabytes.** `ship.py audit --all`,
grouped by directory, then the thirty largest entries. The export dialog reports
a size and nothing about content; everything that must not ship is invisible in
that number. Encrypted? Then export once with encryption off and audit that —
the directory of a release build is unreadable by design.

**2. Ask of every group: does the running game open this?** A pack carries what
the exporter swept, not what the game loads. Textures nothing binds, a contact
sheet whose slices are what the code actually names, an atlas superseded a month
ago — all of it renders identically and weighs the same as content.

**3. Third-party assets are a licence question, not a size one.** A demo scene
from a bought addon, a model from an asset site, a font — shipping them
redistributes someone else's work under your build. This is the finding that
costs money rather than bytes, and no tool can spot it: read `CREDITS`, read the
addon folders, ask where each one came from.

**4. Dotfiles at the root.** Godot skips hidden *directories* and not hidden
*files*, so `.mcp.json`, `.env`, `.something.json` ship under
`export_filter="all_resources"`. This is not theory: a config naming a whole
development toolchain and its ports shipped this way while `--check` printed
"nothing forbidden".

**5. Sidecar files that document the work.** `*.gen.json` next to generated art
holds the prompt and the model that made it; `.import` files are fine, these are
not. Nothing loads them and they read like a diary.

**6. The two lists, against each other.** `audit.forbidden` and the preset's
`exclude_filter` must name the same things. Drift between them is the normal
state of a project, and each gap is invisible from either side alone.

**7. What the artifacts say out loud.** Home directories, credit names, tokens,
the key itself. Two false alarms to know: an engine binary carries the CI paths
of whoever compiled it (`runneradmin` and friends — upstream's machine), and
mbedtls compiles PEM headers in as format strings. Neither is your leak.

**8. What encryption and obfuscation actually buy.** The pack key is compiled
into the exe — it must be, the game reads its own pack — so encryption stops the
curious, not the determined. Obfuscation renames declarations and never touches
string literals or file paths: `"REGISTRY_DEAD"`, `src/sim/shadow_market.gd` and
`signal bribe_taken` survive it. Say this plainly rather than implying a build is
sealed.

**9. What the audit cannot see.** An autoload from a dev addon that is excluded
by file and still named in `project.godot` (three ERROR lines on a player's first
frame — `strip.autoloads`). A debug layer or a dev console shipped but gated. A
source map sitting inside the archive instead of beside it. A green boot check is
a start, not a playtest, and saying so is part of the report.

## The forbidden list is the point

```
py ship.py audit --check     # exit 1 on a violation
py ship.py audit --all       # every file, largest first
```

The two lists in `godot-ship.yaml` are the real specification of a clean build:
`audit.forbidden` (what may never reach a player) and `audit.required` (what the
game does not boot without). **Edit them together with the preset's
`exclude_filter`, never one alone** — the two drifting apart is how a config file
with a whole toolchain in it shipped in one project while `--check` printed
"nothing forbidden".

Two things worth knowing before trusting a clean audit:

- **Godot skips hidden directories, not hidden files.** With
  `export_filter="all_resources"`, a dotfile at the project root with a resource
  extension — `.mcp.json` — ships.
- **An encrypted pack encrypts its directory**, so the audit cannot read a
  release build at all. Audit an unencrypted export of the same tree.

## Obfuscation

```
py ~/.claude/skills/godot-ship/ship.py obfuscate          # install, configure, lock
py ~/.claude/skills/godot-ship/ship.py obfuscate --locks  # regenerate the lock list
py ~/.claude/skills/godot-ship/ship.py obfuscate --check  # report only
```

It installs `addons/gdmaim` when the project has none — from `gdmaim_src` in the
local config if a checkout is named there, otherwise cloned from upstream —
enables the plugin in `project.godot`, writes the settings below into **both**
config files, flips `script_export_mode` to 0, and adds `addons/gdmaim/*` to
every `exclude_filter`. With `obfuscate: true` in `godot-ship.yaml`, a build
re-runs the lock scan and **refuses to export** when a new call site has no entry.

These are settled, and each one is a build that broke:

- **`script_export_mode=0`** in the preset is what makes obfuscation possible —
  Godot's own tokenizer would hand GDMaim a `.gdc` with nothing left to rewrite.
- **`strip_extraneous_spacing=false`.** It turns a wrapped expression whose
  continuation starts with an operator into `+0.15`, which GDScript reads as a
  signed literal, and the script fails to parse at run time.
- **`export_vars=false`** wherever a binary `.res` holds exported properties:
  GDMaim rewrites `.tscn`/`.tres` that set them and cannot rewrite a binary one.
- **`custom_tokens_enabled=true`, or the lock list is decoration.** GDMaim reads
  `ignore_tokens.txt` only behind that flag. With it off, `ship.py obfuscate
  --check` reports every string-reached symbol locked and the export renames
  every one of them — found on a Bridge build whose game never reached the SDK
  once. `review` and a build both refuse that state now.
- **`multi_filepath` holds every store SDK folder**, written from
  `obfuscation.exclude` plus every variant's `addon:` without listing them. The
  game names SDK members in strings and the platform's JavaScript names them
  from outside the pack; a rename breaks it with no error. What the file
  already excluded is kept and printed, so it can be moved into the config.
- **`strip_editor_annotations=false`.** Stripping `@export` makes an inherited
  scene lose every typed node reference its base scene set, and the child
  starts with null exports and dies in `_ready`. The annotations cost nothing.
- **`inject_name=false` is load-bearing, not cosmetic.** It stops a
  `print("GDMaim - Source map '…'")` going into the first autoload — but on 0.3.9
  the same branch also fills the map that registers autoloads as global symbols,
  so turning it off can unlink `Autoload.member()` from its declaration. Check
  the linkage after any GDMaim upgrade.
- **Symbols reached by string** — `call("…")`, `has_method("…")`, `connect("…")`
  — are renamed at the declaration and not at the call site. They belong in
  `addons/gdmaim/user/ignore_tokens.txt`.
- The **source map** in `.dev/gdmaim/` is the only way to read a crash report
  from an obfuscated build. It travels *beside* an archive handed over, never
  inside it, and that folder rotates at ten files.

## What this does not cover

- **Playing it.** A green run says the build starts and its first seconds are
  clean. Nothing more.
- **Signing.** Artifacts are unsigned; SmartScreen greets a stranger, and macOS
  needs a Mac and an Apple identity.
- **macOS templates from Windows.** Not possible — Apple's SDK and linker.
- **`application/modify_resources`.** On some custom templates, letting Godot
  rewrite the exe's PE resources produces a file Windows refuses to start
  (`%1 is not a valid Win32 application`). If the exe needs an icon, put it there
  after the export with a tool built for it.
