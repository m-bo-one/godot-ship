"""GDMaim's exclusion field and the flag that makes the lock list count.

    py -m unittest discover -s tests

No GDMaim here: the two config files are written by hand in the shape the
addon writes them, and only the tool's reading and rewriting of them is tested.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import ship                                   # noqa: E402
from lib import gdmaim as gdmaim_lib          # noqa: E402
from test_variants import ProjectCase          # noqa: E402

EXPORT_CFG = '''[obfuscator]

enabled=true

[post_process]

strip_editor_annotations=true

[exclude_files_category]

multi_filepath="res://addons/vendored_sdk/;res://addons/playgama_bridge/"
custom_tokens_enabled=false
'''


class Paths(unittest.TestCase):
    def test_exclude_paths_normalises_and_deduplicates(self):
        self.assertEqual(
            gdmaim_lib.exclude_paths(["addons/a", "res://addons/a/", "addons\\b\\", " ", "addons/a"]),
            "res://addons/a/;res://addons/b/")
        self.assertEqual(gdmaim_lib.exclude_paths([]), "")


class Files(ProjectCase):
    def write(self, text=EXPORT_CFG):
        for name in gdmaim_lib.CONFIG_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_review_names_the_flag_and_the_missing_folder(self):
        self.write()
        remarks = gdmaim_lib.review(self.root, ["addons/playgama_bridge", "addons/gpx-godot-plugin"])
        self.assertEqual(len(remarks), 2 * len(gdmaim_lib.CONFIG_FILES))
        self.assertTrue(any("custom_tokens_enabled is false" in r for r in remarks))
        self.assertTrue(any("res://addons/gpx-godot-plugin/" in r for r in remarks))
        self.assertFalse(any("vendored_sdk" in r for r in remarks))

    def test_write_settings_turns_the_flag_on_and_keeps_what_the_file_excluded(self):
        self.write()
        gdmaim_lib.write_settings(self.root, ["addons/playgama_bridge", "addons/gpx-godot-plugin"])
        for name in gdmaim_lib.CONFIG_FILES:
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertIn("custom_tokens_enabled=true", text)
            self.assertIn("strip_editor_annotations=false", text)
            line = [l for l in text.splitlines() if l.startswith("multi_filepath=")][0]
            # The config's folders, then the one only the file knew about.
            self.assertEqual(line, 'multi_filepath="res://addons/playgama_bridge/;'
                                   'res://addons/gpx-godot-plugin/;res://addons/vendored_sdk/"')
        self.assertEqual(gdmaim_lib.review(self.root, ["addons/playgama_bridge"]), [])

    def test_write_settings_without_a_file_writes_the_section(self):
        gdmaim_lib.write_settings(self.root, ["addons/playgama_bridge"])
        text = (self.root / gdmaim_lib.CONFIG_FILES[0]).read_text(encoding="utf-8")
        self.assertIn("[exclude_files_category]", text)
        self.assertIn('multi_filepath="res://addons/playgama_bridge/"', text)
        self.assertIn("custom_tokens_enabled=true", text)

    def test_every_variant_addon_is_excluded_without_being_listed(self):
        cfg = self.cfg({"targets": ["web", "playgama"], "obfuscation": {"exclude": ["addons/x"]},
                        "variants": {"playgama": {"platform": "web", "addon": "playgama_bridge"}}})
        self.assertEqual(ship._unobfuscated(cfg), ["addons/x", "addons/playgama_bridge"])

    def test_a_build_refuses_the_flag_off(self):
        """obfuscate --check is what build runs first; the scan alone passed this state."""
        self.write()
        (self.root / "addons/gdmaim").mkdir(parents=True, exist_ok=True)
        (self.root / "addons/gdmaim/plugin.cfg").write_text("[plugin]\n", encoding="utf-8")
        cfg = self.cfg({"targets": ["web"], "obfuscate": True})
        with mock.patch.object(gdmaim_lib, "locks", return_value=([], [], [])):
            ship.obfuscate(cfg, check=True)
        self.assertTrue(any("custom_tokens_enabled is false" in f for f in ship.failures))


if __name__ == "__main__":
    unittest.main()
