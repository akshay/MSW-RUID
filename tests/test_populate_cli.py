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


pop_ruids = load_module("pop_ruids_test", "pop-ruids.py")


class PopulateCliTests(unittest.TestCase):
    def test_parse_category_filters_supports_repeated_and_comma_separated_values(self):
        allowed_categories = pop_ruids._parse_category_filters(["back,object", "npc"])

        self.assertEqual(allowed_categories, {"back", "object", "npc"})

    def test_build_populate_worklist_filters_to_selected_categories(self):
        worklist = pop_ruids._build_populate_worklist(
            ["manual-guid", "back-guid", "object-guid"],
            {
                "back-guid": "back",
                "object-guid": "object",
                "npc-guid": "npc",
            },
            {"back"},
        )

        self.assertEqual(worklist, ["back-guid"])


if __name__ == "__main__":
    unittest.main()
