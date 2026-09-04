"""R5: no GPL/AGPL source trees or verbatim README copies in this repo."""

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
FORBIDDEN_ROOT_FILES = (
    "trendradar.md",
    "trendradar-utf8.md",
    "worldmonitor.md",
    "worldmonitor-utf8.md",
)
FORBIDDEN_TREES = (
    "TrendRadar",
    "WorldMonitor",
    "RohOnChain",
    "vendor/trendradar",
    "vendor/worldmonitor",
    "vendor/TrendRadar",
    "vendor/WorldMonitor",
    "third_party/trendradar",
    "third_party/worldmonitor",
    "src/trendradar",
    "src/worldmonitor",
)
FORBIDDEN_LICENSE_SNIPPETS = (
    "gnu general public license",
    "gnu affero general public license",
)


class LicenseVendoringTests(unittest.TestCase):
    def test_known_gpl_agpl_readme_copies_are_absent(self):
        present = [name for name in FORBIDDEN_ROOT_FILES if (REPO / name).exists()]
        self.assertEqual(present, [])

    def test_known_vendored_source_trees_are_absent(self):
        offenders = [name for name in FORBIDDEN_TREES if (REPO / name).exists()]
        self.assertEqual(offenders, [])

    def test_package_does_not_vendor_copyleft_license_text(self):
        package = REPO / "signal_sim"
        offenders = []
        for path in package.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if any(snippet in text for snippet in FORBIDDEN_LICENSE_SNIPPETS):
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
