# godot-ship

Build any Godot project the same way: export for Windows, macOS and the browser,
check what actually went into the pack, boot the artifact and read its output,
and keep absolute machine paths out of the repository.

## Install

Place this folder in your skills directory:

```
git clone <this repo> ~/.claude/skills/godot-ship
```

That is the whole installation. Nothing is copied into projects; `git pull` there
updates every project at once.

## Use it through Claude

The skill is picked up automatically. Three prompts cover the whole life of a
project:

```
/godot-ship set this project up for shipping
/godot-ship audit the build
/godot-ship build the release
```

| Prompt | What happens |
|---|---|
| **set up** | one pass: presets, key, templates, obfuscation. It asks the six things it cannot read off `project.godot` — targets, encryption, what a clean run means here, obfuscation, what travels beside the exe, what dev addon comes out — and writes both config files. It touches no git configuration. |
| **audit** | what leaks and what nobody covered: the pack read file by file, the two exclusion lists checked against each other, dotfiles that ship, third-party assets you are redistributing, strings readable in the artifacts. A process, not just a script. |
| **build** | export every target, verify, boot the artifact, report sizes and what is still owed by hand. |

Plain words work too — "the build cannot read its own pack", "black screen in the
browser" — the skill carries the answers to those.

## Use it by hand

```
py ~/.claude/skills/godot-ship/ship.py init      # once per project
py ~/.claude/skills/godot-ship/ship.py doctor
py ~/.claude/skills/godot-ship/ship.py build
```

| Command | What |
|---|---|
| `build [targets]` | export, verify, boot |
| `review` | audit the setup: what is not covered, and what the artifacts give away |
| `doctor` | engine, key, templates — what is missing |
| `serve` | open the web build; it cannot run from disk |
| `audit --check` | what is actually inside the pack |
| `boot` | run the artifact and read its output |
| `obfuscate` | install and configure GDMaim, keep its lock list |
| `key` | create the pack encryption key, once |
| `check-paths` | machine paths about to be committed |
| `templates windows --src <godot source>` | a template with the pack key inside |

## Two config files

`godot-ship.yaml` is tracked and says what a build contains and how it is judged.
`.godot-ship.local.yaml` is git-ignored and says where the engine, the templates
and the key live on this computer. A machine path never enters a tracked file —
the preset keeps `custom_template/release` empty and `ship.py` writes the real
one in for the length of the export.

Environment variables win over the local file, so CI needs no file at all:
`GODOT_SHIP_ENGINE`, `GODOT_TEMPLATE_WINDOWS`, `GODOT_SCRIPT_ENCRYPTION_KEY`.

## What it knows

Every check in here was paid for by a build that shipped wrong once — an
encrypting preset with no key in the environment, a dotfile with a whole
toolchain in it, a web export opened from disk. The list is [SKILL.md](SKILL.md).

## License

MIT — see [LICENSE](LICENSE).
