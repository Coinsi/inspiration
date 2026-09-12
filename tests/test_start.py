"""一键启动安全分支测试：python -m unittest discover -s tests -v。"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import start


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = start.Launcher(self.root)
        self.launcher.runtime.mkdir()

    def test_api_health_requires_our_app(self):
        for value, expected in [
            ({"status": "ok", "app": "Inspiration"}, True),
            ({"status": "ok", "app": "Other"}, False),
            ({"status": "down", "app": "Inspiration"}, False),
            ([], False),
        ]:
            with (
                self.subTest(value=value),
                patch.object(start, "http_text", return_value=json.dumps(value)),
            ):
                self.assertEqual(start.api_ready("Inspiration"), expected)

    def test_invalid_health_is_not_reused(self):
        with patch.object(start, "http_text", return_value="not json"):
            self.assertFalse(start.api_ready("Inspiration"))

    def test_unrelated_frontend_not_reused(self):
        with patch.object(
            start, "http_text", return_value='<div id="root">Other</div>'
        ):
            self.assertFalse(start.web_ready())

    def test_running_service_never_starts_docker(self):
        with (
            patch.object(start, "port_open", return_value=True),
            patch.object(self.launcher, "ensure_docker") as docker,
        ):
            self.launcher.ensure_service(
                "postgres", ["localhost", 5432], default_port=5432
            )
            docker.assert_not_called()

    def test_unavailable_custom_services_do_not_switch_data(self):
        cases = [
            ("db.example.com", 5432, True),
            ("localhost", 5433, True),
            ("localhost", 5432, False),
        ]
        for host, port, allowed in cases:
            with (
                self.subTest(host=host, port=port, allowed=allowed),
                patch.object(start, "port_open", return_value=False),
                patch.object(self.launcher, "ensure_docker") as docker,
            ):
                with self.assertRaises(start.StartupError):
                    self.launcher.ensure_service(
                        "postgres", [host, port], default_port=5432, allowed=allowed
                    )
                docker.assert_not_called()

    def test_missing_local_minio_only_starts_minio(self):
        self.launcher.docker = "docker"
        with (
            patch.object(start, "port_open", return_value=False),
            patch.object(start, "wait_for"),
            patch.object(self.launcher, "command") as command,
        ):
            self.launcher.ensure_service(
                "minio", ["127.0.0.1", 9000], default_port=9000
            )
        args = command.call_args.args[0]
        self.assertEqual(args[-4:], ["up", "-d", "--no-deps", "minio"])
        self.assertNotIn("down", args)

    def test_fs_eager_skips_minio_and_redis(self):
        config = {
            "database": ["localhost", 5432],
            "compose_database": False,
            "storage": "fs",
            "eager": True,
        }
        with (
            patch.object(self.launcher, "ensure_service") as service,
            patch.object(self.launcher, "command"),
        ):
            self.launcher.dependencies(config)
        self.assertEqual([c.args[0] for c in service.call_args_list], ["postgres"])

    def test_async_mode_checks_redis(self):
        config = {
            "database": ["localhost", 5432],
            "compose_database": False,
            "storage": "fs",
            "eager": False,
            "redis": ["localhost", 6379],
        }
        with (
            patch.object(self.launcher, "ensure_service") as service,
            patch.object(self.launcher, "command"),
        ):
            self.launcher.dependencies(config)
        self.assertEqual(
            [c.args[0] for c in service.call_args_list], ["postgres", "redis"]
        )

    def test_existing_port_conflict_does_not_kill_any_process(self):
        with (
            patch.object(start, "port_open", return_value=True),
            patch.object(start, "wait_for", side_effect=start.StartupError("not ours")),
            self.assertRaisesRegex(start.StartupError, "端口 8000"),
        ):
            self.launcher.existing(8000, "后端", lambda: False)
        self.assertEqual(self.launcher.started, [])

    def test_repeated_launch_skips_migrations_seed_and_spawns(self):
        with (
            patch.object(
                self.launcher,
                "prepare",
                return_value={"app_name": "Inspiration", "eager": True},
            ),
            patch.object(self.launcher, "existing", return_value=True),
            patch.object(self.launcher, "dependencies"),
            patch.object(start, "wait_for"),
            patch.object(self.launcher, "command") as command,
            patch.object(self.launcher, "spawn") as spawn,
        ):
            self.launcher.start()
        command.assert_not_called()
        spawn.assert_not_called()

    def test_failed_child_is_reported_without_waiting_timeout(self):
        process = Mock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(start.StartupError, "提前退出"):
            start.wait_for("后端", lambda: False, process=process)

    def test_cleanup_only_terminates_owned_live_processes(self):
        live, exited = Mock(), Mock()
        live.poll.return_value = None
        exited.poll.return_value = 0
        self.launcher.started = [live, exited]
        self.launcher.cleanup_failed_start()
        live.terminate.assert_called_once()
        exited.terminate.assert_not_called()

    def test_startup_lock_prevents_concurrent_double_click(self):
        path = self.launcher.runtime / "start.lock"
        with (
            start.startup_lock(path),
            self.assertRaisesRegex(start.StartupError, "另一个启动窗口"),
            start.startup_lock(path),
        ):
            self.fail("second startup should not enter")
        with start.startup_lock(path):
            pass  # 成功后可以再次启动，锁文件的存在不会阻止后续启动。

    def test_prepare_preserves_existing_env(self):
        self.launcher.backend.mkdir()
        self.launcher.python.parent.mkdir(parents=True)
        self.launcher.python.touch()
        vite = self.launcher.frontend / "node_modules/vite/bin/vite.js"
        vite.parent.mkdir(parents=True)
        vite.touch()
        env_file = self.launcher.backend / ".env"
        content = "# Existing configuration\nCELERY_EAGER=true\n"
        env_file.write_text(content)
        responses = [
            subprocess.CompletedProcess([], 0, stdout="v22.0.0"),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout='{"eager": true}'),
        ]
        with (
            patch.object(start.shutil, "which", return_value="node"),
            patch.object(self.launcher, "command", side_effect=responses),
        ):
            self.launcher.prepare()
        self.assertEqual(env_file.read_text(), content)


if __name__ == "__main__":
    unittest.main()
