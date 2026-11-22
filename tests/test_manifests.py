import json
import tempfile
import unittest
from pathlib import Path

from contract.support import ROOT, module
from listening_plugin import Candidate
from listening_plugin.contract import LIMIT
from listening_plugin.runtime import candidates, check_methods, dispatch
from tools.plugins import catalog, discover


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.shipped = discover(ROOT / "plugins")
        self.manifest = dict(self.shipped["example-catalog"], id="third-party", sources={"search": ["public"]})

    def test_two_file_search_only_plugin(self):
        with tempfile.TemporaryDirectory() as target:
            path = Path(target) / "third-party"
            path.mkdir()
            (path / "manifest.json").write_text(json.dumps(self.manifest))
            (path / "provider.py").write_text(
                "from listening_plugin import Candidate\n"
                "class Provider:\n"
                "    def __init__(self, context): pass\n"
                "    def test(self): pass\n"
                "    def search(self, recording): return [Candidate('public', recording.title)]\n")
            from importlib.util import module_from_spec, spec_from_file_location
            spec = spec_from_file_location("fixture_provider", path / "provider.py")
            provider = module_from_spec(spec)
            spec.loader.exec_module(provider)
            manifest = discover(path.parent)["third-party"]
            check_methods(provider.Provider, manifest)
            api = provider.Provider(None)
            result = dispatch(api, manifest["sources"], {"action": "search", "version": 1,
                              "recording": {"artist": "Artist", "title": "Song", "formats": [".mp3"]}})
            self.assertEqual(result["candidates"], [{"source": "public", "id": "Song"}])
            with self.assertRaises(ValueError):
                dispatch(api, manifest["sources"], {"action": "fetch", "version": 1})
            with self.assertRaisesRegex(ValueError, "fetch"):
                check_methods(provider.Provider, dict(manifest, sources={"fetch": ["public"]}))

    def test_release_catalog_preserves_settings_and_requires_pinned_unique_images(self):
        digest = "registry.test/plugin@sha256:" + "0" * 64
        ref = "sockseek/amd64=" + digest
        entry, = catalog(self.shipped, [ref])["engines"]
        self.assertEqual(entry["settings"], self.shipped["sockseek"]["settings"])
        self.assertEqual(entry["sources"], {"search": ["soulseek"], "fetch": ["soulseek"]})
        for images in ([ref, ref], ["sockseek/arm64=" + digest], ["sockseek/amd64=registry.test/plugin:latest"]):
            with self.subTest(images=images), self.assertRaises(ValueError):
                catalog(self.shipped, images)

    def test_large_unicode_candidates_fit_in_the_room_reply(self):
        ranked = [Candidate("public", chr(0x10000 + i) * 512) for i in range(20)]
        result = candidates(ranked, ["public"])
        self.assertTrue(0 < len(result) < 20)
        self.assertEqual([item["id"] for item in result], [item.id for item in ranked[:len(result)]])
        self.assertLessEqual(len(json.dumps({"ok": True, "candidates": result, "version": 1})) + 1, LIMIT)

    def test_shipped_providers_implement_declared_methods(self):
        for name, manifest in self.shipped.items():
            check_methods(module(name, "provider").Provider, manifest)
