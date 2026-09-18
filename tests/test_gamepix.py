"""GamePix beside Playgama: a second store SDK, and each build carrying only its own.

    py -m unittest discover -s tests

No network and no Godot: the plugin zip is built here with the layout the real
one has (a single `gpx-godot-plugin/` folder), and the exporter is a fake.
"""

from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import ship                                   # noqa: E402
from lib import gamepix as gamepix_lib        # noqa: E402
from lib import playgama as playgama_lib      # noqa: E402
from lib import sdk as sdk_lib                # noqa: E402
from test_variants import PRESETS, PROJECT_GODOT, ProjectCase   # noqa: E402

# What plugin.gd appends through ConfigFile when no preset name begins with
# "GamePix": no exclude list, no custom_template keys, only the shell.
BARE = '''
[preset.9]

name="GamePix"
platform="Web"
runnable=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""

[preset.9.options]

html/custom_html_shell="res://addons/gpx-godot-plugin/index.html"
'''


def plugin_zip() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("gpx-godot-plugin/plugin.cfg",
                         '[plugin]\n\nname="gamepix-sdk"\nversion="1.2"\nscript="plugin.gd"\n')
        archive.writestr("gpx-godot-plugin/plugin.gd", "@tool extends EditorPlugin\n")
        archive.writestr("gpx-godot-plugin/gpx.gd", "extends Node\n")
        archive.writestr("gpx-godot-plugin/index.html", "<html></html>\n")
    return data.getvalue()


class Install(ProjectCase):
    def test_install_from_a_local_zip(self):
        source = self.tmp / "gpx-godot-plugin-1.2r.zip"
        source.write_bytes(plugin_zip())
        with mock.patch.object(sdk_lib, "fetch", side_effect=AssertionError("no network")):
            gamepix_lib.install(self.root, str(source))
        # Under the name plugin.gd hardcodes, not the zip's and not the file's.
        self.assertTrue((self.root / "addons/gpx-godot-plugin/plugin.cfg").is_file())
        self.assertTrue((self.root / "addons/gpx-godot-plugin/index.html").is_file())
        self.assertTrue(gamepix_lib.installed(self.root))
        self.assertEqual(gamepix_lib.version(self.root), "v1.2")

    def test_install_downloads_when_no_source(self):
        with mock.patch.object(sdk_lib, "fetch", return_value=plugin_zip()) as fetch:
            gamepix_lib.install(self.root, None)
        fetch.assert_called_once_with(gamepix_lib.ARCHIVE)
        self.assertTrue(gamepix_lib.installed(self.root))

    def test_download_failure_names_the_docs_page(self):
        with mock.patch.object(sdk_lib, "fetch", side_effect=OSError("404")):
            with self.assertRaises(SystemExit) as caught:
                gamepix_lib.install(self.root, None)
        self.assertIn(gamepix_lib.DOCS, str(caught.exception))
        self.assertIn("gamepix_src", str(caught.exception))

    def test_enable_puts_gpx_first_and_registers_the_plugin(self):
        gamepix_lib.enable(self.root)
        text = (self.root / "project.godot").read_text(encoding="utf-8")
        self.assertIn('[autoload]\n\nGPX="*res://addons/gpx-godot-plugin/gpx.gd"\nBridge=', text)
        self.assertIn('"res://addons/playgama_bridge/plugin.cfg", '
                      '"res://addons/gpx-godot-plugin/plugin.cfg")', text)
        gamepix_lib.enable(self.root)
        self.assertEqual((self.root / "project.godot").read_text(encoding="utf-8"), text)


class Presets(ProjectCase):
    def setUp(self):
        super().setUp()
        (self.root / "addons/playgama_bridge").mkdir(parents=True)
        (self.root / "addons/playgama_bridge/plugin.cfg").write_text(
            '[plugin]\nversion="2.2.0"\n', encoding="utf-8")

    def blocks(self) -> dict[str, str]:
        text = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        found = {}
        for name in ("Web", "Web Playgama", "GamePix", "Windows Desktop"):
            cfg = self.cfg({"variants": {"v": {"platform": "web", "preset": name}}})
            span = ship._preset_span(text, cfg.variant("v"))
            if span:
                found[name] = span[2]
        return found

    def test_split_writes_gamepix_and_holds_it_out_of_the_rest(self):
        done = gamepix_lib.split_presets(self.root, "GamePix", gamepix_lib.OUT, [playgama_lib])
        blocks = self.blocks()
        store = blocks["GamePix"]
        self.assertIn(f'html/custom_html_shell="{gamepix_lib.SHELL}"', store)
        self.assertIn(f'export_path="{gamepix_lib.OUT}"', store)
        exclude = [line for line in store.splitlines() if line.startswith("exclude_filter=")][0]
        self.assertIn("addons/playgama_bridge/*", exclude)     # the rival SDK out
        self.assertNotIn("gpx-godot-plugin", exclude)          # its own in
        self.assertIn("tools/*", exclude)                      # the project's own list kept
        self.assertIn("custom_template/release=", store)       # so the template can be written
        self.assertIn("[preset.3.options]", store)
        # Every other WEB preset excludes the plugin; the desktop one is not touched.
        for name in ("Web", "Web Playgama"):
            self.assertIn("addons/gpx-godot-plugin/*", blocks[name])
        self.assertNotIn("gpx-godot-plugin", blocks["Windows Desktop"])
        # The Playgama preset keeps its own shell and its own addon.
        self.assertIn(playgama_lib.SHELL, blocks["Web Playgama"])
        self.assertNotIn("addons/playgama_bridge/*", blocks["Web Playgama"])
        self.assertEqual(len(done), 3)
        # Idempotent: a second run changes nothing.
        before = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        again = gamepix_lib.split_presets(self.root, "GamePix", gamepix_lib.OUT, [playgama_lib])
        self.assertEqual(len(again), 1)
        self.assertEqual((self.root / "export_presets.cfg").read_text(encoding="utf-8"), before)

    def test_copy_comes_from_the_plain_web_preset(self):
        """"Web Playgama" is first in the file; the copy must not inherit its shell
        source or miss the Bridge exclusion."""
        gamepix_lib.split_presets(self.root, "GamePix", gamepix_lib.OUT, [playgama_lib])
        self.assertNotIn(playgama_lib.SHELL, self.blocks()["GamePix"])

    def test_a_name_without_the_prefix_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            gamepix_lib.split_presets(self.root, "Web GamePix", gamepix_lib.OUT)
        self.assertIn("begin with", str(caught.exception))

    def test_playgama_after_gamepix_holds_bridge_out_of_gamepix(self):
        (self.root / "addons/gpx-godot-plugin").mkdir(parents=True)
        (self.root / "addons/gpx-godot-plugin/plugin.cfg").write_text("[plugin]\n", encoding="utf-8")
        text = PRESETS.replace(",addons/playgama_bridge/*", "") + BARE.replace(
            'exclude_filter=""', 'exclude_filter="tools/*"')
        (self.root / "export_presets.cfg").write_text(text, encoding="utf-8")
        playgama_lib.split_presets(self.root, "Web Playgama", playgama_lib.OUT, [gamepix_lib])
        blocks = self.blocks()
        self.assertIn("addons/playgama_bridge/*", blocks["GamePix"])
        self.assertIn("addons/playgama_bridge/*", blocks["Web"])


class Review(ProjectCase):
    def test_bare_gamepix_preset_is_warned_about(self):
        warnings = gamepix_lib.bare_presets(PRESETS + BARE)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("empty exclude_filter" in w for w in warnings))
        self.assertTrue(any("custom_template/release" in w for w in warnings))
        self.assertEqual(gamepix_lib.bare_presets(PRESETS), [])

    def test_the_preset_the_tool_writes_is_not_bare(self):
        gamepix_lib.split_presets(self.root, "GamePix", gamepix_lib.OUT)
        text = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        self.assertEqual(gamepix_lib.bare_presets(text), [])

    def test_each_web_variant_carries_only_the_sdk_it_names(self):
        for module in (playgama_lib, gamepix_lib):
            (self.root / module.ADDON).mkdir(parents=True)
            (self.root / module.ADDON / "plugin.cfg").write_text("[plugin]\n", encoding="utf-8")
        providers = [playgama_lib, gamepix_lib]
        clean = {"autoloads": ["Bridge"], "plugins": [playgama_lib.PLUGIN]}
        # The GamePix build, Bridge fully out: nothing to say.
        self.assertEqual(sdk_lib.foreign(self.root, gamepix_lib, providers,
                                         "tools/*,addons/playgama_bridge/*", clean), [])
        # Bridge's files still ship.
        found = sdk_lib.foreign(self.root, gamepix_lib, providers, "tools/*", clean)
        self.assertEqual(len(found), 1)
        self.assertIn("addons/playgama_bridge/*", found[0])
        # Bridge still registered, and its plugin still enabled.
        found = sdk_lib.foreign(self.root, gamepix_lib, providers,
                                "addons/playgama_bridge/*", {"autoloads": [], "plugins": []})
        self.assertEqual(len(found), 2)
        # The plain web build names no SDK, so it must carry none.
        found = sdk_lib.foreign(self.root, None, providers, "addons/playgama_bridge/*", clean)
        self.assertTrue(all("GPX" in w or "gpx-godot-plugin" in w for w in found))
        self.assertEqual(len(found), 3)

    def test_an_sdk_that_is_not_installed_is_not_asked_about(self):
        self.assertEqual(sdk_lib.foreign(self.root, None, [playgama_lib, gamepix_lib], "",
                                         {"autoloads": [], "plugins": []}), [])


class Post(ProjectCase):
    def variant(self, post):
        cfg = self.cfg({"targets": ["v"], "variants": {"v": {
            "platform": "web", "preset": "Web", "archive": "build/v.zip", "post": post}}})
        return cfg, cfg.variant("v")

    def test_post_runs_from_the_project_root_and_its_exit_fails_the_build(self):
        cfg, variant = self.variant("the project's command")
        (self.root / "build").mkdir()
        (self.root / "build/v.zip").write_bytes(b"zip")
        with mock.patch.object(ship.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            ship.run_post(cfg, variant)
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)
        self.assertTrue(run.call_args.kwargs["shell"])
        self.assertEqual(ship.failures, [])
        with mock.patch.object(ship.subprocess, "run", return_value=mock.Mock(returncode=3)):
            ship.run_post(cfg, variant)
        self.assertEqual(len(ship.failures), 1)
        self.assertIn("exited 3", ship.failures[0])

    def test_post_is_skipped_after_a_failure_and_when_unset(self):
        cfg, variant = self.variant("anything")
        ship.failures.append("the export is already bad")
        with mock.patch.object(ship.subprocess, "run") as run:
            ship.run_post(cfg, variant)
            ship.failures.clear()
            ship.run_post(cfg, cfg.variant("web"))
        run.assert_not_called()

    def test_build_runs_post_after_the_archive(self):
        cfg, _ = self.variant("pack")
        order = []

        def fake(cmd, *args, **kwargs):
            if kwargs.get("shell"):
                order.append(("post", (self.root / "build/v.zip").is_file()))
                return mock.Mock(returncode=0)
            order.append(("export", None))
            out = self.root / "build/web"
            out.mkdir(parents=True, exist_ok=True)
            for name in ("index.html", "index.js", "index.wasm", "index.pck"):
                (out / name).write_text("x", encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch.object(ship, "preflight", return_value="godot"), \
             mock.patch.object(ship.subprocess, "run", side_effect=fake), \
             mock.patch.object(ship, "boot"):
            ship.build(cfg, ["v"])
        self.assertEqual(order, [("export", None), ("post", True)])


if __name__ == "__main__":
    unittest.main()
