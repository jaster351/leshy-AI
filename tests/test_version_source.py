from pathlib import Path
import unittest

import leshy
from leshy.version import VERSION, read_version, version_file, version_payload


class CanonicalVersionTests(unittest.TestCase):
    def test_version_file_is_single_runtime_source(self):
        path = version_file()
        self.assertEqual(path.name, "VERSION")
        self.assertEqual(path.read_text(encoding="utf-8").strip(), VERSION)
        self.assertEqual(read_version(), VERSION)
        self.assertEqual(leshy.__version__, VERSION)

    def test_version_has_semver_shape(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

    def test_runtime_payload_uses_same_version(self):
        self.assertEqual(version_payload()["version"], VERSION)

    def test_package_init_does_not_hardcode_version_literal(self):
        init_file = Path(leshy.__file__).resolve()
        text = init_file.read_text(encoding="utf-8")
        self.assertNotIn(f'__version__ = "{VERSION}"', text)
        self.assertNotIn(f"__version__ = '{VERSION}'", text)
        self.assertIn("from .version import VERSION as __version__", text)

    def test_runtime_python_files_do_not_duplicate_current_version_literal(self):
        package_root = Path(leshy.__file__).resolve().parent
        forbidden = (f'"{VERSION}"', f"'{VERSION}'")
        duplicates = []
        for path in package_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(literal in text for literal in forbidden):
                duplicates.append(str(path.relative_to(package_root)))
        self.assertEqual(
            duplicates,
            [],
            "Текущая версия должна задаваться только в корневом VERSION; "
            f"найдены дубликаты: {duplicates}",
        )


if __name__ == "__main__":
    unittest.main()
