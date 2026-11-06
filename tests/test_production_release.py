import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_manifest", ROOT / "deploy/production/release_manifest.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SHA = "a" * 40
MANIFEST = {
    "version": 2,
    "release": SHA,
    "schema": "b" * 64,
    "images": {
        role: f"ghcr.io/coinsi/inspiration-{role}@sha256:" + "c" * 64
        for role in ("api", "web")
    },
}


class ReleaseBundleTests(unittest.TestCase):
    def bundle(self, root, mutate=None, extra=None):
        data = {name: b"test" for name in module.FILES}
        manifest = json.loads(json.dumps(MANIFEST))
        if mutate:
            mutate(manifest)
        data["release.json"] = json.dumps(manifest).encode()
        data["registry-auth.json"] = (
            b'{"username":"runner","password":"temporary-secret"}'
        )
        path = root / "release.tgz"
        with tarfile.open(path, "w:gz") as archive:
            for name, content in data.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            if extra:
                archive.addfile(extra)
        return path

    def test_valid_bundle_credentials_only_in_temporary_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.unpack(self.bundle(root), root, SHA)
            self.assertFalse((root / "registry-auth.json").exists())
            self.assertEqual(json.loads((root / "release.json").read_text()), MANIFEST)
            self.assertIn(
                MANIFEST["images"]["api"], (root / "release-images.env").read_text()
            )
            self.assertNotIn(
                "temporary-secret", (root / "release-images.env").read_text()
            )
            self.assertTrue((root / "docker-auth/config.json").is_file())

    def test_wrong_commit_and_untrusted_image_rejected_before_extraction(self):
        for mutation in (
            lambda m: m.update(release="d" * 40),
            lambda m: m["images"].update(api="ghcr.io/other/image:latest"),
            lambda m: m.update(schema="$(command)"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(ValueError):
                    module.unpack(self.bundle(root, mutation), root, SHA)
                self.assertFalse((root / "compose.yml").exists())

    def test_extra_traversal_duplicate_and_symlink_rejected(self):
        for name, kind in (
            ("../escape", tarfile.REGTYPE),
            ("compose.yml", tarfile.REGTYPE),
            ("link", tarfile.SYMTYPE),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                with self.assertRaises(ValueError):
                    module.unpack(self.bundle(root, extra=info), root, SHA)


@unittest.skipIf(sys.platform == "win32", "Deploy shell integration runs on Linux CI")
class DeployFlowTests(unittest.TestCase):
    def run_deploy(self, *, schema_change=False, api_change=False, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "releases" / ("d" * 40)
            current = root / "releases" / SHA
            for directory in (previous, current):
                directory.mkdir(parents=True)
                (directory / "compose.yml").write_text("test")
                (directory / "release-images.env").write_text(
                    "API_IMAGE=api:old\nWEB_IMAGE=web:old\n"
                )
                (directory / "release.json").write_text(json.dumps({"schema": "old"}))
            (root / "current").symlink_to(previous, target_is_directory=True)
            (root / "runtime.env").touch()
            if api_change:
                (current / "release-images.env").write_text(
                    "API_IMAGE=api:new\nWEB_IMAGE=web:new\n"
                )
            if schema_change:
                (current / "release.json").write_text(json.dumps({"schema": "new"}))
            script = (
                (ROOT / "deploy/production/deploy.sh")
                .read_text()
                .replace("root=/opt/inspiration", f"root={root}")
            )
            (root / "deploy.sh").write_text(script)
            executable = root / "docker"
            executable.write_text(
                "#!/bin/bash\n"
                + f'echo "$*" >> "{root}/calls"\n'
                + 'if [[ "$1" == inspect ]]; then echo api:old; fi\n'
                + (
                    'if [[ "$*" == *"--wait-timeout"* && "$*" == *"'
                    + SHA
                    + '"* ]]; then exit 1; fi\n'
                    if fail
                    else ""
                )
            )
            executable.chmod(0o755)
            import os

            result = subprocess.run(
                ["bash", str(root / "deploy.sh"), SHA],
                env={**os.environ, "PATH": str(root) + ":" + os.environ["PATH"]},
                capture_output=True,
                text=True,
                check=False,
            )
            calls = (root / "calls").read_text()
            selected = (root / "current").resolve().name
            return result, calls, selected

    def test_frontend_only_does_not_stop_workers_or_migrate(self):
        result, calls, selected = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(" stop ", calls)
        self.assertNotIn("pg_dump", calls)
        self.assertNotIn("alembic upgrade", calls)
        self.assertEqual(selected, SHA)

    def test_backend_update_backs_up_and_drains_worker(self):
        result, calls, _ = self.run_deploy(api_change=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stop -t 120 worker", calls)
        self.assertIn("pg_dump", calls)
        self.assertNotIn("alembic upgrade", calls)

    def test_schema_change_keeps_backup_and_migration(self):
        result, calls, _ = self.run_deploy(schema_change=True, api_change=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(calls.index("pg_dump"), calls.index("alembic upgrade"))

    def test_health_failure_restores_previous_application(self):
        result, calls, selected = self.run_deploy(api_change=True, fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(selected, "d" * 40)
        self.assertIn("restoring previous", result.stdout)
        self.assertIn("d" * 40 + "/compose.yml up", calls)

    def test_schema_change_failure_does_not_automatically_roll_back_database(self):
        result, _calls, selected = self.run_deploy(
            schema_change=True, api_change=True, fail=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(selected, "d" * 40)
        self.assertNotIn("restoring previous", result.stdout)


if __name__ == "__main__":
    unittest.main()
