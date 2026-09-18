"""CrazyGames: an SDK with two autoloads, no shell, and no download.

    py -m unittest discover -s tests

The archive is built here with the layout the real one has -- a Godot 3 and a
Godot 4 addon side by side -- and nothing touches the network.
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

import ship                                     # noqa: E402
from lib import crazygames as crazygames_lib    # noqa: E402
from lib import gamepix as gamepix_lib          # noqa: E402
from lib import playgama as playgama_lib        # noqa: E402
from lib import sdk as sdk_lib                  # noqa: E402
from lib import yamlish                         # noqa: E402
from test_variants import PRESETS, ProjectCase  # noqa: E402

PLUGIN_CFG = '[plugin]\n\nname="CrazyGames SDK"\nauthor="CrazyGames"\nversion="1.0.1"\nscript="sdk_load.gd"\n'


def store_zip(folders=("crazysdk-godot-3", "crazysdk-godot-4")) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for folder in folders:
            archive.writestr(f"{folder}/plugin.cfg", PLUGIN_CFG)
            archive.writestr(f"{folder}/sdk_load.gd", "@tool\nextends EditorPlugin\n")
            archive.writestr(f"{folder}/CrazyGames.gd", f"# {folder}\nextends Node\n")
            archive.writestr(f"{folder}/Utils/CrazyGamesBridge.gd", "extends Node\n")
    return data.getvalue()


class Install(ProjectCase):
    def test_the_godot_4_addon_is_picked_out_of_the_archive(self):
        source = self.tmp / "crazysdk.zip"
        source.write_bytes(store_zip())
        crazygames_lib.install(self.root, str(source))
        main = (self.root / "addons/crazygames/CrazyGames.gd").read_text(encoding="utf-8")
        self.assertIn("crazysdk-godot-4", main)       # not -3, which sorts first
        self.assertTrue((self.root / "addons/crazygames/Utils/CrazyGamesBridge.gd").is_file())
        self.assertEqual(crazygames_lib.version(self.root), "v1.0.1")

    def test_the_unpacked_archive_works_as_a_source_too(self):
        unpacked = self.tmp / "unpacked"
        with zipfile.ZipFile(io.BytesIO(store_zip())) as archive:
            archive.extractall(unpacked)
        crazygames_lib.install(self.root, str(unpacked))
        self.assertIn("crazysdk-godot-4",
                      (self.root / "addons/crazygames/CrazyGames.gd").read_text(encoding="utf-8"))

    def test_an_archive_with_only_godot_3_is_refused(self):
        source = self.tmp / "old.zip"
        source.write_bytes(store_zip(("crazysdk-godot-3", "something-else")))
        with self.assertRaises(SystemExit) as caught:
            crazygames_lib.install(self.root, str(source))
        self.assertIn("crazysdk-godot-4", str(caught.exception))

    def test_no_source_says_where_a_person_gets_it(self):
        with mock.patch.object(sdk_lib, "fetch", side_effect=AssertionError("no network")):
            with self.assertRaises(SystemExit) as caught:
                crazygames_lib.install(self.root, None)
        self.assertIn(crazygames_lib.STORE, str(caught.exception))
        self.assertIn("crazygames_src", str(caught.exception))

    def test_a_lookalike_addon_under_the_same_folder_is_refused(self):
        folder = self.root / "addons/crazygames"
        folder.mkdir(parents=True)
        (folder / "plugin.cfg").write_text('[plugin]\nname="CrazyGames SDK"\n', encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            crazygames_lib.install(self.root, None)
        self.assertIn("not the official addon", str(caught.exception))

    def test_enable_writes_both_autoloads_first_and_in_load_order(self):
        crazygames_lib.enable(self.root)
        text = (self.root / "project.godot").read_text(encoding="utf-8")
        self.assertIn('[autoload]\n\n'
                      'CrazyGamesBridge="*res://addons/crazygames/Utils/CrazyGamesBridge.gd"\n'
                      'CrazyGames="*res://addons/crazygames/CrazyGames.gd"\n'
                      'Bridge=', text)
        self.assertIn('"res://addons/crazygames/plugin.cfg")', text)
        crazygames_lib.enable(self.root)
        self.assertEqual((self.root / "project.godot").read_text(encoding="utf-8"), text)


class Presets(ProjectCase):
    def block(self, name: str) -> str:
        text = (self.root / "export_presets.cfg").read_text(encoding="utf-8")
        cfg = self.cfg({"variants": {"v": {"platform": "web", "preset": name}}})
        return ship._preset_span(text, cfg.variant("v"))[2]

    def test_the_preset_keeps_godots_shell_and_gets_the_css(self):
        (self.root / "addons/playgama_bridge").mkdir(parents=True)
        (self.root / "addons/playgama_bridge/plugin.cfg").write_text("[plugin]\n", encoding="utf-8")
        done = crazygames_lib.split_presets(self.root, "CrazyGames", crazygames_lib.OUT,
                                            [playgama_lib, gamepix_lib])
        store = self.block("CrazyGames")
        self.assertIn('html/custom_html_shell=""', store)          # no shell of its own
        self.assertIn(f'export_path="{crazygames_lib.OUT}"', store)
        self.assertIn("user-select:none", store)
        exclude = [l for l in store.splitlines() if l.startswith("exclude_filter=")][0]
        self.assertIn("addons/playgama_bridge/*", exclude)         # an installed rival: out
        self.assertNotIn("gpx-godot-plugin", exclude)              # one that is not installed: not named
        self.assertNotIn("addons/crazygames", exclude)
        for name in ("Web", "Web Playgama"):
            self.assertIn("addons/crazygames/*", self.block(name))
            self.assertNotIn("user-select", self.block(name))      # only the preset it wrote
        self.assertTrue(any("user-select" in line for line in done))
        self.assertEqual(crazygames_lib.review(
            self.root, (self.root / "export_presets.cfg").read_text(encoding="utf-8")), [])

    def test_css_is_appended_to_an_existing_head_include(self):
        text = PRESETS.replace('html/custom_html_shell=""',
                               'html/custom_html_shell=""\nhtml/head_include="<script>x()</script>"')
        (self.root / "export_presets.cfg").write_text(text, encoding="utf-8")
        crazygames_lib.split_presets(self.root, "CrazyGames", crazygames_lib.OUT)
        self.assertIn(f'html/head_include="<script>x()</script>{crazygames_lib.NO_SELECT}"',
                      self.block("CrazyGames"))
        self.assertNotIn('"', crazygames_lib.NO_SELECT)            # it lives inside a quoted value

    def test_review_warns_about_a_hand_made_preset(self):
        by_hand = PRESETS + '''
[preset.7]

name="CrazyGames"
platform="Web"
exclude_filter="tools/*"
export_path="build/crazygames/game.html"

[preset.7.options]

custom_template/release=""
html/custom_html_shell=""
'''
        warnings = crazygames_lib.review(self.root, by_hand)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("user-select" in w for w in warnings))
        self.assertTrue(any("index.html" in w for w in warnings))


class Limits(ProjectCase):
    def export(self, files: int, size: int) -> Path:
        folder = self.root / "build/crazygames/web"
        folder.mkdir(parents=True)
        for index in range(files):
            (folder / f"f{index}").write_bytes(b"")
        with open(folder / "index.pck", "wb") as handle:
            handle.truncate(size)          # sparse: a 300 MB file without 300 MB of writes
        return folder

    def test_file_count_and_total_size_fail(self):
        bad, _ = crazygames_lib.check_export(self.export(1500, 1))
        self.assertEqual(len(bad), 1)
        self.assertIn("1501 files", bad[0])

    def test_total_over_250_mb_fails(self):
        bad, _ = crazygames_lib.check_export(self.export(1, 251 * 1048576))
        self.assertTrue(any("250 MB" in what for what in bad))

    def test_initial_download_budgets_warn(self):
        bad, iffy = crazygames_lib.check_export(self.export(1, 60 * 1048576))
        self.assertEqual(bad, [])
        self.assertTrue(any("gameplay_start" in what for what in iffy))

    def test_mobile_homepage_budget_warns_and_a_small_build_is_quiet(self):
        _, iffy = crazygames_lib.check_export(self.export(1, 21 * 1048576))
        self.assertTrue(any("mobile homepage" in what for what in iffy))

    def test_a_small_build_is_quiet(self):
        self.assertEqual(crazygames_lib.check_export(self.export(3, 1048576)), ([], []))

    def test_build_reports_the_stores_limits(self):
        cfg = self.cfg({"targets": ["crazygames"], "variants": {"crazygames": {
            "platform": "web", "preset": "Web", "out": "build/crazygames/web/index.html",
            "addon": "crazygames"}}})
        (self.root / "addons/crazygames/Utils").mkdir(parents=True)
        (self.root / "addons/crazygames/plugin.cfg").write_text(PLUGIN_CFG, encoding="utf-8")

        def fake(cmd, *args, **kwargs):
            out = self.root / "build/crazygames/web"
            out.mkdir(parents=True, exist_ok=True)
            for name in ("index.html", "index.js", "index.wasm"):
                (out / name).write_text("x", encoding="utf-8")
            with open(out / "index.pck", "wb") as handle:
                handle.truncate(300 * 1048576)
            return mock.Mock(returncode=0)

        with mock.patch.object(ship, "preflight", return_value="godot"), \
             mock.patch.object(ship.subprocess, "run", side_effect=fake), \
             mock.patch.object(ship, "boot"):
            with self.assertRaises(SystemExit):
                ship.build(cfg, ["crazygames"])
        self.assertTrue(any("250 MB" in what for what in ship.failures))


class OnlyItsOwnSdk(ProjectCase):
    def test_both_autoloads_of_a_rival_must_be_stripped(self):
        (self.root / "addons/crazygames").mkdir(parents=True)
        (self.root / "addons/crazygames/plugin.cfg").write_text(PLUGIN_CFG, encoding="utf-8")
        providers = [playgama_lib, gamepix_lib, crazygames_lib]
        found = sdk_lib.foreign(self.root, None, providers, "addons/crazygames/*",
                                {"autoloads": ["CrazyGames"], "plugins": [crazygames_lib.PLUGIN]})
        self.assertEqual(len(found), 1)
        self.assertIn("CrazyGamesBridge", found[0])
        self.assertNotIn("CrazyGames,", found[0])
        clean = {"autoloads": ["CrazyGamesBridge", "CrazyGames"], "plugins": [crazygames_lib.PLUGIN]}
        self.assertEqual(sdk_lib.foreign(self.root, None, providers, "addons/crazygames/*", clean), [])
        # The variant that names it is not asked to strip it.
        self.assertEqual(sdk_lib.foreign(self.root, crazygames_lib, providers, "",
                                         {"autoloads": [], "plugins": []}), [])

    def test_the_printed_yaml_strips_both_names_and_parses(self):
        printed = [l for l in sdk_lib.starter(crazygames_lib, [playgama_lib])
                   if not l.startswith("targets:")]
        data = yamlish.loads("\n".join(printed), prefer_pyyaml=False)
        self.assertEqual(data["variants"]["crazygames"]["addon"], "crazygames")
        self.assertEqual(data["variants"]["playgama"]["strip"]["autoloads"],
                         ["CrazyGamesBridge", "CrazyGames"])
        self.assertEqual(data["variants"]["web"]["strip"]["autoloads"],
                         ["CrazyGamesBridge", "CrazyGames", "Bridge"])

    def test_it_is_registered(self):
        self.assertIs(ship.ADDONS["crazygames"], crazygames_lib)
        self.assertEqual(crazygames_lib.SHELL, "")


if __name__ == "__main__":
    unittest.main()
