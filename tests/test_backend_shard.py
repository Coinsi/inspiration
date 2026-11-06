import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backend_shard", ROOT / "deploy/ci/backend_shard.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ShardTests(unittest.TestCase):
    def test_partition_has_no_missing_or_duplicate_files_and_is_order_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for i in range(25):
                path = Path(tmp) / f"test_{i}.py"
                path.write_bytes(b"x" * (i + 1))
                files.append(path)
            groups = module.partition(files, 4)
            flattened = [p for group in groups for p in group]
            self.assertEqual(set(flattened), set(files))
            self.assertEqual(len(flattened), len(files))
            self.assertEqual(groups, module.partition(list(reversed(files)), 4))
            self.assertTrue(all(groups))

    def test_all_current_backend_test_files_are_assigned(self):
        files = set((ROOT / "backend/tests").rglob("test_*.py"))
        files.update((ROOT / "backend/tests").rglob("*_test.py"))
        groups = module.partition(files, 4)
        self.assertEqual(sum(map(len, groups)), len(files))
        self.assertTrue(all(groups))


if __name__ == "__main__":
    unittest.main()
