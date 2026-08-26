"""Every heuristic table in one file, because these are what gets edited.

The code that uses them is stable; the lists are not. A new dev folder, a new
sidecar convention, a new secret shape -- each is one line here rather than a
regex hunted down in whichever module happened to grow it.

Nothing in this file knows about any particular project. Per-project answers live
in `godot-ship.yaml`: this is the starting point `ship.py init` proposes and the
net `ship.py review` casts.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- what ships

# What Godot's exporter treats as a resource and sweeps into the pack under
# `export_filter="all_resources"`. A dotfile only matters when it is one of
# these: `.gitignore` cannot ship, `.mcp.json` can, and did.
RESOURCE_EXT = {
    ".json", ".cfg", ".tres", ".res", ".tscn", ".scn", ".gd", ".gdshader", ".txt",
    ".csv", ".png", ".jpg", ".jpeg", ".svg", ".ogg", ".wav", ".ttf", ".otf", ".md",
}

# Godot's own cache. Parts of it ship by design -- imported textures, exported
# scenes -- so naming it would be noise on every project.
NEVER_FLAG = {".godot"}

# ------------------------------------------------- what a project carries too

# Directories that exist to build the game, not to be in it.
DEV_DIR = re.compile(r"tools?|scripts?|docs?|build|dist|shots?|blender")

# File classes worth forbidding only when the project actually has some.
# pattern -> the glob that decides whether it does.
SIDECARS = {
    "*.gen.json": "**/*.gen.json",   # the prompt and model that generated an asset
    "*.blend": "**/*.blend",         # source art beside the exported mesh
    "*.TMP": "**/*.TMP",             # an editor's leftovers
    "*.blend1": "**/*.blend1",
}

# A compute shader rules the browser out. Godot's web export runs on the
# compatibility renderer, which has no compute stage at all: the export succeeds,
# the page loads, and whatever the shader was doing simply does not happen.
COMPUTE_SHADERS = ["**/*.glsl", "**/*.slang"]

# True of every project, whether or not it has one today.
#
# This list is also `config.DEFAULTS["audit"]["forbidden"]` -- imported there
# rather than copied. The two were written out twice and drifted: the defaults
# named six patterns this list did not, so the moment `init` wrote a config the
# project was covered by a SMALLER net than a project with no config at all,
# and `audit --check` said "nothing forbidden" over the difference.
HYGIENE = [
    ".keys/*", ".git/*", ".vscode/*", ".claude/*", ".dev/*", ".gdmaim/*",
    "addons/gdmaim/*", "build/*", "docs/*", "tools/*",
    "*.md", "*.py", "*.ps1", "*.bat", "*.cmd", "*.log", "*.tmp", "*.TMP",
]

# Top-level entries `review` remarks on when nothing names them.
SUSPECT = [
    (re.compile(rf"^({DEV_DIR.pattern})$"), "development-only directory"),
    (re.compile(r".*\.(bat|cmd|ps1|sh|py)$"), "a script the game never runs"),
]

# ------------------------------------------------------ absolute machine paths

# Windows drive paths, UNC shares, and the two shapes of a Unix home directory.
# `res://` and `uid://` are not matched: the colon there is preceded by letters
# and followed by two slashes, and a drive letter is exactly one character.
# A UNC host never starts with a dot and a share is a name, not punctuation --
# without both, an escaped regex in a config file (`\\.7\\.exe`) reads as one.
MACHINE_PATHS = [
    (re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>|]*"), "drive path"),
    (re.compile(r"\\\\[A-Za-z0-9_-][A-Za-z0-9_.-]*\\[A-Za-z0-9_$-][^\s\"'<>|]*"), "UNC share"),
    (re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/[^\s\"'<>|]*"), "home directory"),
]

# A binary file has no lines worth reporting, and a false positive in one is
# noise nobody can act on. `.gltf` is deliberately NOT here: it is the plain-JSON
# half of the format, and its "uri" entries are exactly where an exporter writes
# an absolute path through somebody's home directory. Only `.glb` is binary.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".ico", ".ctex", ".res", ".scn", ".pck",
    ".exe", ".dll", ".zip", ".7z", ".wasm", ".ttf", ".otf", ".ogg", ".wav",
    ".mp3", ".blend", ".glb", ".translation", ".bin", ".dat",
}

# --------------------------------------------- what must not be readable

# Two of these were written badly the first time and said so out loud on a build
# that was fine, which is worse than saying nothing:
#
# * an engine binary carries the build paths of whoever compiled it, and for an
#   official build that is a CI runner's home directory -- upstream's machine,
#   not this user's. Only THIS user's home counts as a leak; any other is a remark.
# * mbedtls compiles the PEM header in as a FORMAT STRING. A header alone proves
#   nothing; key material means base64 following it.
LEAKS = [
    (re.compile(rb"(?:api[_-]?key|secret[_-]?key|password)\s*[=:]\s*['\"][^'\"]{6,}"),
     "something that reads like a credential"),
    (re.compile(rb"(?:ghp_|xox[baprs]-|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"), "an API token"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=]{40}"),
     "a private key"),
]

# Any home directory in an artifact, so the owner of it can be judged.
ANY_HOME = re.compile(rb"(?:[A-Za-z]:[\\/])?(?:Users|home)[\\/]([A-Za-z0-9_.-]+)[\\/]", re.I)
