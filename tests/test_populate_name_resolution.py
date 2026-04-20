import importlib.util
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pop_ruids = load_module("pop_ruids_name_test", "pop-ruids.py")


class PopulateNameResolutionTests(unittest.TestCase):
    def test_normalize_store_rewrites_guid_based_fallback_name(self):
        all_tags = {
            "tile-guid-123": "guid-123",
            "tile-164": "guid-123",
        }

        pop_ruids._normalize_tag_store("tile", all_tags)

        self.assertEqual(all_tags, {"tile-164": "guid-123"})

    def test_guid_needs_reprocessing_when_only_fallback_tag_exists(self):
        needs_reprocessing = pop_ruids._guid_needs_reprocessing(
            "tile",
            "guid-123",
            {
                "tile-guid-123": "guid-123",
            },
        )

        self.assertTrue(needs_reprocessing)


if __name__ == "__main__":
    unittest.main()
