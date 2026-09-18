"""Export variants: one platform, several presets, each stripped on its own.

    py -m unittest discover -s tests

Standard library only, like the rest of the tool. Every test builds a throwaway
project in a temp directory; nothing here reads a real one or runs Godot -- the
exporter subprocess is replaced by a fake that records what project.godot and
export_presets.cfg said at the moment of the export.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ship                                   # noqa: E402
from lib import config as config_lib          # noqa: E402
from lib import playgama as playgama_lib      # noqa: E402
from lib import review as review_lib          # noqa: E402
from lib import yamlish                       # noqa: E402

PROJECT_GODOT = '''config_version=5

[application]

config/name="Game"
run/main_scene="res://main.tscn"
config/features=PackedStringArray("4.7", "Forward Plus")

[autoload]

Bridge="*res://addons/playgama_bridge/bridge.gd"
Platform="*res://autoload/platform.gd"
QaDriver="*res://tools/qa_driver.gd"

[editor_plugins]

enabled=PackedStringArray("res://addons/gdmaim/plugin.cfg", "res://addons/playgama_bridge/plugin.cfg")
'''

# The store preset comes FIRST on purpose: a lookup by platform would land on it
# for the plain `web` target, which is the bug variants exist to close.
PRESETS = '''[preset.0]

name="Web Playgama"
platform="Web"
runnable=true
export_filter="all_resources"
exclude_filter="tools/*,addons/gdmaim/*"
export_path="build/playgama/web/index.html"
encrypt_pck=false
encrypt_directory=false

[preset.0.options]

custom_template/debug=""
custom_template/release=""
html/custom_html_shell="res://addons/playgama_bridge/template/index.html"

[preset.1]

name="Web"
platform="Web"
runnable=true
export_filter="all_resources"
exclude_filter="tools/*,addons/gdmaim/*,addons/playgama_bridge/*"
export_path="build/web/index.html"
encrypt_pck=false
encrypt_directory=false

[preset.1.options]

custom_template/debug=""
custom_template/release=""
html/custom_html_shell=""

[preset.2]

name="Windows Desktop"
platform="Windows Desktop"
runnable=true
export_filter="all_resources"
exclude_filter="tools/*,addons/gdmaim/*"
export_path="build/Game.exe"
encrypt_pck=false
encrypt_directory=false

[preset.2.options]

custom_template/debug=""
custom_template/release=""
'''

TRACKED = {
    "targets": ["web", "playgama"],
    "strip": {"autoloads": ["QaDriver"]},
    "variants": {
        "playgama": {
            "platform": "web",
            "preset": "Web Playgama",
            "out": "build/playgama/web/index.html",
            "strip": {"autoloads": ["QaDriver"]},
        },
        "web": {
            "strip": {"autoloads": ["QaDriver", "Bridge"],
                      "plugins": ["res://addons/playgama_bridge/plugin.cfg"]},
        },
    },
}


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="godot-ship-test-"))
        self.root = self.tmp / "game"
        self.root.mkdir()
        (self.root / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
        (self.root / "export_presets.cfg").write_text(PRESETS, encoding="utf-8")
        ship.failures.clear()
        self._env = dict(os.environ)
        for name in list(os.environ):
            if name.startswith("GODOT_TEMPLATE_"):
                del os.environ[name]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, tracked=None, local=None) -> config_lib.Config:
        return config_lib.Config(self.root, tracked if tracked is not None else TRACKED,
                                 local or {})


class StripPerVariant(ProjectCase):
    def test_variant_strip_applied_and_restored(self):
        cfg = self.cfg()
        original_manifest = (self.root / "project.godot").read_text(encoding="utf-8")
        original_presets = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        seen: dict[str, str] = {}
        commands: list[list[str]] = []

        def fake_export(cmd, *args, **kwargs):
            commands.append(cmd)
            preset = cmd[cmd.index("--preset") + 1] if "--preset" in cmd else "Web"
            seen[preset] = (self.root / "project.godot").read_text(encoding="utf-8")
            out = self.root / (cmd[cmd.index("--out") + 1] if "--out" in cmd else "build/web/index.html")
            out.parent.mkdir(parents=True, exist_ok=True)
            for name in ("index.html", "index.js", "index.wasm", "index.pck"):
                (out.parent / name).write_text("x", encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch.object(ship, "preflight", return_value="godot"), \
             mock.patch.object(ship.subprocess, "run", side_effect=fake_export), \
             mock.patch.object(ship, "boot"):
            ship.build(cfg, ["web", "playgama"])

        self.assertIn("Web", seen)
        self.assertIn("Web Playgama", seen)
        # The plain web build: both autoloads and the plugin gone.
        self.assertNotIn("Bridge=", seen["Web"])
        self.assertNotIn("QaDriver=", seen["Web"])
        self.assertNotIn("playgama_bridge/plugin.cfg", seen["Web"])
        self.assertIn("Platform=", seen["Web"])
        # The store build: its own list, not the union -- Bridge stays.
        self.assertIn("Bridge=", seen["Web Playgama"])
        self.assertNotIn("QaDriver=", seen["Web Playgama"])
        self.assertIn("playgama_bridge/plugin.cfg", seen["Web Playgama"])
        # Both tracked files are back, byte for byte.
        self.assertEqual((self.root / "project.godot").read_text(encoding="utf-8"), original_manifest)
        self.assertEqual((self.root / "export_presets.cfg").read_text(encoding="utf-8"), original_presets)
        # One process per variant, the variant's preset named to it.
        self.assertEqual(len(commands), 2)
        self.assertIn("--preset", commands[1])
        self.assertEqual(commands[1][commands[1].index("--preset") + 1], "Web Playgama")
        self.assertEqual(commands[1][commands[1].index("--out") + 1], "build/playgama/web/index.html")
        self.assertEqual(commands[1][-1], "web")

    def test_restored_when_the_export_raises(self):
        cfg = self.cfg()
        original = (self.root / "project.godot").read_text(encoding="utf-8")
        with mock.patch.object(ship, "preflight", return_value="godot"), \
             mock.patch.object(ship.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaises(SystemExit):
                ship.build(cfg, ["web"])
        self.assertEqual((self.root / "project.godot").read_text(encoding="utf-8"), original)

    def test_plain_target_keeps_the_old_behaviour(self):
        """No `variants:` at all: one process, no --preset, the top-level strip."""
        cfg = self.cfg({"targets": ["web"], "strip": {"autoloads": ["QaDriver"]}})
        commands = []

        def fake_export(cmd, *args, **kwargs):
            commands.append(cmd)
            self.assertNotIn("QaDriver=", (self.root / "project.godot").read_text(encoding="utf-8"))
            return mock.Mock(returncode=1)

        with mock.patch.object(ship, "preflight", return_value="godot"), \
             mock.patch.object(ship.subprocess, "run", side_effect=fake_export):
            with self.assertRaises(SystemExit):
                ship.build(cfg, ["web"])
        self.assertEqual(len(commands), 1)
        self.assertNotIn("--preset", commands[0])
        self.assertNotIn("--out", commands[0])


class TemplateLookup(ProjectCase):
    def test_variant_falls_back_to_its_platform(self):
        template = self.tmp / "web.zip"
        template.write_bytes(b"zip")
        cfg = self.cfg(local={"template": {"web": str(template)}})
        self.assertEqual(cfg.template("playgama"), str(template))
        self.assertEqual(cfg.template("web"), str(template))

    def test_variant_name_wins_over_platform(self):
        shared, own = self.tmp / "web.zip", self.tmp / "playgama.zip"
        shared.write_bytes(b"zip")
        own.write_bytes(b"zip")
        cfg = self.cfg(local={"template": {"web": str(shared), "playgama": str(own)}})
        self.assertEqual(cfg.template("playgama"), str(own))
        self.assertEqual(cfg.template("web"), str(shared))

    def test_environment_by_variant_then_platform(self):
        shared, own = self.tmp / "web.zip", self.tmp / "playgama.zip"
        shared.write_bytes(b"zip")
        own.write_bytes(b"zip")
        cfg = self.cfg()
        os.environ["GODOT_TEMPLATE_WEB"] = str(shared)
        self.assertEqual(cfg.template("playgama"), str(shared))
        os.environ["GODOT_TEMPLATE_PLAYGAMA"] = str(own)
        self.assertEqual(cfg.template("playgama"), str(own))

    def test_missing_file_is_no_template(self):
        cfg = self.cfg(local={"template": {"web": str(self.tmp / "gone.zip")}})
        self.assertIsNone(cfg.template("playgama"))


class Resolution(ProjectCase):
    def test_unknown_target_rejected(self):
        cfg = self.cfg()
        with self.assertRaises(SystemExit) as caught:
            cfg.variant("steam")
        self.assertIn("steam", str(caught.exception))
        with self.assertRaises(SystemExit):
            ship.preflight(cfg, ["steam"])

    def test_bad_platform_rejected(self):
        cfg = self.cfg({"variants": {"x": {"platform": "linux"}}})
        with self.assertRaises(SystemExit):
            cfg.variant("x")

    def test_platform_defaults(self):
        cfg = self.cfg({"targets": ["windows"]})
        variant = cfg.variant("windows")
        self.assertEqual((variant.platform, variant.preset, variant.explicit),
                         ("windows", "Windows Desktop", False))
        self.assertIsNone(variant.strip)
        self.assertEqual(cfg.strip_for(variant), {"autoloads": [], "plugins": []})

    def test_variant_strip_replaces_not_merges(self):
        cfg = self.cfg()
        self.assertEqual(cfg.strip_for(cfg.variant("playgama"))["autoloads"], ["QaDriver"])
        cfg = self.cfg({"strip": {"autoloads": ["A"]}, "variants": {"v": {"platform": "web"}}})
        self.assertEqual(cfg.strip_for(cfg.variant("v"))["autoloads"], ["A"])
        cfg = self.cfg({"strip": {"autoloads": ["A"]},
                        "variants": {"v": {"platform": "web", "strip": {}}}})
        self.assertEqual(cfg.strip_for(cfg.variant("v"))["autoloads"], [])

    def test_encrypt_by_platform_covers_variants(self):
        cfg = self.cfg({"encrypt": {"web": True}, "variants": {"v": {"platform": "web"}}})
        self.assertTrue(cfg.encrypts("v"))
        cfg = self.cfg({"encrypt": {"web": True, "v": False}, "variants": {"v": {"platform": "web"}}})
        self.assertFalse(cfg.encrypts("v"))

    def test_artifact_path_by_preset_name(self):
        cfg = self.cfg()
        self.assertEqual(ship.artifact_path(cfg, "playgama"),
                         self.root / "build/playgama/web/index.html")
        # Not the first Web preset in the file -- the one called "Web".
        self.assertEqual(ship.artifact_path(cfg, "web"), self.root / "build/web/index.html")
        cfg = self.cfg({"variants": {"v": {"platform": "web", "preset": "Web Playgama"}}})
        self.assertEqual(ship.artifact_path(cfg, "v"), self.root / "build/playgama/web/index.html")

    def test_explicit_variant_never_falls_back_to_platform(self):
        cfg = self.cfg({"variants": {"v": {"platform": "web", "preset": "Nope"}}})
        text = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        self.assertIsNone(ship._preset_span(text, cfg.variant("v")))
        self.assertIsNotNone(ship._preset_span(text, cfg.variant("windows")))


class Halves(ProjectCase):
    def test_both_halves_or_neither(self):
        exclude = "tools/*,addons/playgama_bridge/*"
        bad, iffy = review_lib.halves(self.root, exclude, {"autoloads": ["QaDriver"], "plugins": []})
        self.assertEqual(len(bad), 1)
        self.assertIn("Bridge", bad[0])
        bad, iffy = review_lib.halves(self.root, exclude,
                                      {"autoloads": ["QaDriver", "Bridge"],
                                       "plugins": ["res://addons/playgama_bridge/plugin.cfg"]})
        self.assertEqual(bad, [])
        self.assertEqual(iffy, [])
        # Dropped from project.godot while its files still ship: a warning.
        bad, iffy = review_lib.halves(self.root, "tools/*", {"autoloads": ["QaDriver", "Bridge"],
                                                            "plugins": []})
        self.assertEqual(bad, [])
        self.assertTrue(any("Bridge" in w for w in iffy))
        # The plugin dropped, its autoload kept: the plugin puts it back.
        bad, iffy = review_lib.halves(self.root, "tools/*",
                                      {"autoloads": ["QaDriver"],
                                       "plugins": ["res://addons/playgama_bridge/plugin.cfg"]})
        self.assertTrue(any("re-registers" in w for w in iffy))


class Playgama(ProjectCase):
    def test_split_presets(self):
        one_web = PRESETS.split("[preset.1]")[1]
        (self.root / "export_presets.cfg").write_text(
            "[preset.0]" + one_web.replace("[preset.1.options]", "[preset.0.options]")
            .replace('html/custom_html_shell=""', f'html/custom_html_shell="{playgama_lib.SHELL}"'),
            encoding="utf-8")
        done = playgama_lib.split_presets(self.root, "Web Playgama", "build/playgama/web/index.html")
        self.assertEqual(len(done), 2)
        text = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        cfg = self.cfg()
        plain = ship._preset_span(text, cfg.variant("web"))[2]
        store = ship._preset_span(text, cfg.variant("playgama"))[2]
        self.assertIn('html/custom_html_shell=""', plain)
        self.assertIn("addons/playgama_bridge/*", plain)
        self.assertIn(f'html/custom_html_shell="{playgama_lib.SHELL}"', store)
        self.assertNotIn("addons/playgama_bridge", store.split("export_path")[0])
        self.assertIn('export_path="build/playgama/web/index.html"', store)
        # Idempotent.
        self.assertEqual(len(playgama_lib.split_presets(self.root, "Web Playgama", "x")), 1)

    def test_enable_registers_autoload_first(self):
        (self.root / "project.godot").write_text(
            PROJECT_GODOT.replace('Bridge="*res://addons/playgama_bridge/bridge.gd"\n', "")
            .replace(', "res://addons/playgama_bridge/plugin.cfg"', ""), encoding="utf-8")
        playgama_lib.enable(self.root)
        text = (self.root / "project.godot").read_text(encoding="utf-8")
        self.assertIn("[autoload]\n\nBridge=", text)
        self.assertIn(playgama_lib.PLUGIN, text)
        before = text
        playgama_lib.enable(self.root)
        self.assertEqual((self.root / "project.godot").read_text(encoding="utf-8"), before)


class Starter(unittest.TestCase):
    def test_variants_example_parses_without_pyyaml(self):
        """The commented example in the generated config, uncommented, must be
        readable by lib/yamlish -- and so must the block `ship.py playgama` prints."""
        starter = ship._starter("4.7", [], [], [[["project.binary"], 1]], [])
        block = starter[starter.index("# variants:"):]
        lines = []
        for line in block.splitlines():
            if line == "#" or not line.startswith("#"):
                break       # a bare `#` separates the commented examples
            lines.append(line[2:] if line.startswith("# ") else line[1:])
        data = yamlish.loads("\n".join(lines), prefer_pyyaml=False)
        self.assertEqual(data["variants"]["playgama"]["preset"], "Web Playgama")
        self.assertEqual(data["variants"]["web"]["strip"]["autoloads"], ["Bridge"])
        printed = [l for l in playgama_lib.STARTER if not l.startswith("targets:")]
        data = yamlish.loads("\n".join(printed), prefer_pyyaml=False)
        self.assertEqual(data["variants"]["playgama"]["addon"], "playgama_bridge")


if __name__ == "__main__":
    unittest.main()
