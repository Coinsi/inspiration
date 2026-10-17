"""一键启动安全分支测试：python -m unittest discover -s tests -v。"""

import io
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
        with patch.object(start, "http_text", return_value='<div id="root">Other</div>'):
            self.assertFalse(start.web_ready())

    def test_running_service_never_starts_docker(self):
        with (
            patch.object(start, "port_open", return_value=True),
            patch.object(self.launcher, "ensure_docker") as docker,
        ):
            self.launcher.ensure_service("postgres", ["localhost", 5432], default_port=5432)
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
            self.launcher.ensure_service("minio", ["127.0.0.1", 9000], default_port=9000)
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
        self.assertEqual([c.args[0] for c in service.call_args_list], ["postgres", "redis"])

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
            patch.object(self.launcher, "command", return_value=Mock(returncode=0)) as command,
            patch.object(self.launcher, "spawn") as spawn,
            patch.object(start.HTTP, "open"),
            patch.object(start.json, "load", return_value={"status": "ok", "optional": {}, "background_workers": {}}),
        ):
            self.launcher.start()
        self.assertEqual(command.call_count, 1)
        self.assertIn("database_ready", command.call_args.args[0][-1])
        spawn.assert_not_called()

    def test_failed_child_is_reported_without_waiting_timeout(self):
        process = Mock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(start.StartupError, "提前退出"):
            start.wait_for("后端", lambda: False, process=process)

    def test_running_api_with_old_schema_is_not_silently_reused(self):
        with (
            patch.object(self.launcher, "prepare", return_value={"app_name": "Inspiration"}),
            patch.object(self.launcher, "existing", return_value=True),
            patch.object(self.launcher, "dependencies"),
            patch.object(self.launcher, "optional_services"),
            patch.object(self.launcher, "command", return_value=Mock(returncode=1)) as command,
            patch.object(self.launcher, "spawn") as spawn,
        ):
            with self.assertRaisesRegex(start.StartupError, "数据库版本"):
                self.launcher.start()
        self.assertEqual(command.call_count, 1)
        spawn.assert_not_called()

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
            subprocess.CompletedProcess([], 0, stdout='{"eager": true}'),
        ]
        with (
            patch.object(start.shutil, "which", return_value="node"),
            patch.object(self.launcher, "command", side_effect=responses),
            patch.object(self.launcher, "prepare_frontend"),
            patch.object(self.launcher, "prepare_backend"),
        ):
            self.launcher.prepare()
        self.assertEqual(env_file.read_text(), content)

    def backend_fixture(self):
        self.launcher.python.parent.mkdir(parents=True)
        (self.launcher.backend / "pyproject.toml").write_text("[project]\nname='test'\n")
        return self.launcher.python.parent.parent / ".inspiration-dependencies"

    def test_backend_adopts_verified_environment_without_install(self):
        stamp = self.backend_fixture()
        with patch.object(self.launcher, "command", return_value=Mock(returncode=0)) as command:
            self.launcher.prepare_backend()
        self.assertTrue(stamp.read_text())
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args.args[0][-2:], ["pip", "check"])

    def test_backend_dependency_change_installs_and_verifies(self):
        stamp = self.backend_fixture()
        stamp.write_text("old")
        with (
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "command", return_value=Mock(returncode=0)) as command,
        ):
            self.launcher.prepare_backend()
        self.assertIn("install", command.call_args_list[0].args[0])
        self.assertEqual(command.call_count, 3)
        self.assertNotEqual(stamp.read_text(), "old")

    def test_backend_does_not_install_into_running_api(self):
        stamp = self.backend_fixture()
        stamp.write_text("old")
        with (
            patch.object(start, "port_open", return_value=True),
            patch.object(self.launcher, "command") as command,
            self.assertRaisesRegex(start.StartupError, "8000"),
        ):
            self.launcher.prepare_backend()
        command.assert_not_called()
        self.assertEqual(stamp.read_text(), "old")

    def test_backend_failed_verification_does_not_stamp(self):
        stamp = self.backend_fixture()
        with (
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "command", side_effect=[
                Mock(returncode=1), Mock(returncode=0), Mock(returncode=1),
            ]),
            self.assertRaisesRegex(start.StartupError, "检查未通过"),
        ):
            self.launcher.prepare_backend()
        self.assertFalse(stamp.exists())

    def test_processing_failure_is_visible_in_summary(self):
        with (
            patch.object(self.launcher, "ensure_processing_service", side_effect=start.StartupError("offline")),
            patch.object(start, "say") as output,
        ):
            self.launcher.processing_services({
                "library_processing_enabled": True,
                "library_wise_url": "http://127.0.0.1:8014", "library_wise_token": "private",
            })
            self.launcher.summary()
        self.assertEqual(self.launcher.service_results, {"wise": "unavailable", "mediacms": "not_configured"})
        rendered = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("部分服务未就绪", rendered)
        self.assertNotIn("private", rendered)
        self.assertNotIn("启动成功", rendered)

    def test_reused_backend_requires_freshness_confirmation(self):
        self.launcher.reused_backend = True
        with patch.object(start, "say") as output:
            self.launcher.summary()
        self.assertTrue(any("无法确认" in call.args[0] for call in output.call_args_list))

    def frontend_fixture(self):
        folder = self.launcher.frontend
        (folder / "node_modules/vite/bin").mkdir(parents=True)
        (folder / "node_modules/vite/bin/vite.js").touch()
        (folder / "package.json").write_text('{"dependencies":{"vite":"5.0.0"}}')
        (folder / "package-lock.json").write_text('{"lockfileVersion":3}')
        return folder

    def test_frontend_syncs_existing_vite_and_reuses_healthy_install(self):
        folder = self.frontend_fixture()
        with (
            patch.object(start.shutil, "which", return_value="npm"),
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "command", return_value=Mock(returncode=0)) as command,
        ):
            self.launcher.prepare_frontend()
            self.assertEqual(command.call_args_list[0].args[0], ["npm", "ci", "--include=dev"])
            command.reset_mock()
            self.launcher.prepare_frontend()
            self.assertEqual(command.call_count, 1)
            self.assertIn("ls", command.call_args.args[0])
            (folder / "package-lock.json").write_text('{"lockfileVersion":3,"changed":true}')
            command.reset_mock()
            self.launcher.prepare_frontend()
            self.assertIn("ci", command.call_args_list[0].args[0])

    def test_frontend_repairs_missing_dependency_even_with_current_stamp(self):
        self.frontend_fixture()
        with (
            patch.object(start.shutil, "which", return_value="npm"),
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "command", return_value=Mock(returncode=0)) as command,
        ):
            self.launcher.prepare_frontend()
            command.reset_mock()
            command.side_effect = [
                Mock(returncode=1),
                Mock(returncode=0),
                Mock(returncode=0),
            ]
            self.launcher.prepare_frontend()
            self.assertIn("ci", command.call_args_list[1].args[0])

    def test_frontend_does_not_replace_live_dependencies_or_stamp_failed_install(self):
        folder = self.frontend_fixture()
        with (
            patch.object(start.shutil, "which", return_value="npm"),
            patch.object(start, "port_open", return_value=True),
            patch.object(self.launcher, "command") as command,
        ):
            with self.assertRaisesRegex(start.StartupError, "5173"):
                self.launcher.prepare_frontend()
            command.assert_not_called()
        with (
            patch.object(start.shutil, "which", return_value="npm"),
            patch.object(start, "port_open", return_value=False),
            patch.object(
                self.launcher,
                "command",
                side_effect=start.StartupError("install failed"),
            ),
        ):
            with self.assertRaisesRegex(start.StartupError, "install failed"):
                self.launcher.prepare_frontend()
        self.assertFalse((folder / "node_modules/.inspiration-dependencies").exists())

    def test_optional_services_do_not_download_or_install(self):
        with (
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "spawn") as spawn,
        ):
            self.launcher.optional_services({"transcriber_url": "http://127.0.0.1:8012"})
        spawn.assert_not_called()

    def test_optional_service_token_is_env_only(self):
        binary = (
            self.launcher.runtime
            / "transcriber"
            / ("Scripts/python.exe" if start.os.name == "nt" else "bin/python")
        )
        binary.parent.mkdir(parents=True)
        binary.touch()
        model = self.launcher.runtime / "models/faster-whisper-small/config.json"
        model.parent.mkdir(parents=True)
        model.write_text("{}")

        def capture(args, *unused):
            self.assertNotIn("fixture-private-token", str(args))
            self.assertEqual(self.launcher.env["ASR_TOKEN"], "fixture-private-token")

        with (
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "spawn", side_effect=capture) as spawn,
        ):
            self.launcher.optional_services(
                {
                    "transcriber_url": "http://127.0.0.1:8012",
                    "transcriber_token": "fixture-private-token",
                }
            )
        spawn.assert_called_once()
        self.assertNotIn("ASR_TOKEN", self.launcher.env)


class ProcessingStartupTests(unittest.TestCase):
    """No Docker or HTTP mutations: exercise decisions against explicit fixtures."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = start.Launcher(self.root)
        self.launcher.runtime.mkdir()
        self.launcher.docker = "docker"
        self.launcher.env.pop("WISE_RUNTIME", None)
        self.folder = self.root / "deploy/media-services"
        self.folder.mkdir(parents=True)
        self.private = self.folder / ".env"
        self.private.write_text("# private fixture, never replace\n")
        self.token = "fixture-private-processing-token"
        self.definition = None
        self.rows = {}
        self.calls = []
        self.missing_image = False
        self.cache_ok = True
        self.stale_env = False
        self.stale_volume = False
        self.stale_image = False
        self.stale_image_id = False
        self.stale_capabilities = False
        self.start_missing = False
        self.device_requests = None

    def fixture(self, name="wise", states=None, *, speech=False):
        self.name = name
        self.url = "http://127.0.0.1:" + ("9671" if name == "wise" else "18080")
        self.definition = {
            "name": "inspiration-wise-services" if name == "wise" else "inspiration-media-services",
            "services": {},
            "volumes": {"cache": {"name": "inspiration-media-services_cache"}},
        }
        names = ["wise"] if name == "wise" else ["db", "redis", "web", "worker", "beat", "init"]
        for service in names:
            env = (
                {
                    "WISE_BRIDGE_TOKEN": self.token,
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "WISE_BRIDGE_CAPABILITIES": "visual,audio",
                }
                if name == "wise"
                else {
                    "MEDIACMS_SERVICE_TOKEN": self.token,
                    "USE_WHISPER_TRANSCRIBE": str(speech).lower(),
                    "WHISPER_MODEL": "base",
                }
            )
            self.definition["services"][service] = {
                "image": f"prepared-{service}",
                "environment": env,
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "published": "9671" if name == "wise" else "18080",
                    }
                ],
                "volumes": [
                    {
                        "type": "volume",
                        "source": "cache",
                        "target": "/home/mediacms.io/.cache",
                    }
                ],
            }
        if name == "mediacms":
            self.rows["init"] = {
                "ID": "init-id",
                "Service": "init",
                "State": "exited",
                "ExitCode": 0,
            }
        for service, state in (states or {}).items():
            self.rows[service] = {
                "ID": service + "-id",
                "Service": service,
                "State": state,
                "Health": "",
            }
        self.config = {
            "library_processing_enabled": True,
            f"library_{name}_url": self.url,
            f"library_{name}_token": self.token,
        }

    def command(self, args, **kwargs):
        self.calls.append((args, kwargs))
        # Expanded Compose config / inspect may contain secrets. All output
        # stays in memory and never takes the launcher's startup.log path.
        self.assertTrue(kwargs.get("capture"))
        self.assertNotIn(self.token, str(args))
        out, code = "", 0
        if "config" in args:
            out = json.dumps(self.definition)
        elif "ps" in args:
            out = json.dumps(list(self.rows.values()))
        elif args[1:3] == ["image", "inspect"]:
            code = 1 if self.missing_image else 0
            out = "sha256:" + start.hashlib.sha256(args[3].encode()).hexdigest()
        elif args[1:2] == ["inspect"]:
            service = args[2].removesuffix("-id")
            definition = self.definition["services"][service]
            env = {**definition["environment"]}
            if self.stale_env:
                env[next(iter(env))] = "old-config-value"
            if self.stale_capabilities:
                env["WISE_BRIDGE_CAPABILITIES"] = "visual,audio"
            out = json.dumps(
                [
                    {
                        "Id": start.hashlib.sha256(service.encode()).hexdigest(),
                        "Image": "sha256:"
                        + (
                            "f" * 64
                            if self.stale_image_id
                            else start.hashlib.sha256(definition["image"].encode()).hexdigest()
                        ),
                        "Config": {
                            "Image": "old-image" if self.stale_image else definition["image"],
                            "Env": [f"{k}={v}" for k, v in env.items()],
                        },
                        "HostConfig": {
                            "DeviceRequests": self.device_requests
                            if self.device_requests is not None
                            else [
                                {
                                    "Driver": d["driver"],
                                    "Count": d["count"],
                                    "Capabilities": [d["capabilities"]],
                                    "DeviceIDs": None,
                                    "Options": None,
                                }
                                for d in definition.get("deploy", {})
                                .get("resources", {})
                                .get("reservations", {})
                                .get("devices", [])
                            ]
                        },
                        "Mounts": [
                            {
                                "Destination": mount["target"],
                                "Name": "different-old-cache"
                                if self.stale_volume
                                else self.definition["volumes"][mount["source"]]["name"],
                            }
                            for mount in definition.get("volumes", [])
                            if mount["type"] == "volume"
                        ],
                    }
                ]
            )
        elif args[1:2] == ["start"]:
            self.assertEqual(args[2], start.hashlib.sha256(b"wise").hexdigest())
            if self.start_missing:
                code = 1
                self.rows.pop("wise", None)
            else:
                self.rows["wise"].update(ID=args[2], State="running", Health="healthy")
        elif "up" in args:
            for service in args[args.index("--no-recreate") + 1 :]:
                self.rows[service] = {
                    "ID": service + "-id",
                    "Service": service,
                    "State": "running",
                    "Health": "healthy",
                }
        elif args[1:2] == ["run"]:
            code = 0 if self.cache_ok else 1
        return subprocess.CompletedProcess(args, code, stdout=out)

    def launch(self, ready=False):
        # Once the fixture receives up, the new endpoint becomes ready.
        def is_ready(*unused):
            endpoint = "wise" if self.name == "wise" else "web"
            return ready or self.rows.get(endpoint, {}).get("State") == "running"

        with (
            patch.object(start, "processing_ready", side_effect=is_ready),
            patch.object(start, "port_open", return_value=False),
            patch.object(self.launcher, "command", side_effect=self.command),
        ):
            self.launcher.ensure_processing_service(self.name, self.url, self.token)

    def test_config_query_reads_new_processing_settings_without_shell_credentials(self):
        for key in (
            "library_processing_enabled",
            "library_wise_url",
            "library_wise_token",
            "library_mediacms_url",
            "library_mediacms_token",
        ):
            self.assertIn(key, start.CONFIG_QUERY)
        self.assertNotIn(self.token, start.CONFIG_QUERY)

    def test_disabled_or_unconfigured_services_do_nothing(self):
        with patch.object(self.launcher, "ensure_processing_service") as ensure:
            self.launcher.processing_services(
                {
                    "library_processing_enabled": False,
                    "library_wise_url": "http://127.0.0.1:9671",
                }
            )
            self.launcher.processing_services({"library_processing_enabled": True})
        ensure.assert_not_called()

    def test_missing_token_warns_and_other_service_still_checked(self):
        config = {
            "library_processing_enabled": True,
            "library_wise_url": "http://127.0.0.1:9671",
            "library_mediacms_url": "http://127.0.0.1:18080",
            "library_mediacms_token": self.token,
        }
        with (
            patch.object(self.launcher, "ensure_processing_service") as ensure,
            patch.object(start, "say") as say,
        ):
            self.launcher.processing_services(config)
        ensure.assert_called_once_with("mediacms", config["library_mediacms_url"], self.token)
        self.assertNotIn(self.token, str(say.call_args_list))

    def test_remote_and_custom_reverse_proxy_never_launch_local(self):
        for url in (
            "https://media.example.test",
            "http://127.0.0.1:9999/proxy",
            "http://[::1]:9671",
        ):
            for ready in (True, False):
                with (
                    self.subTest(url=url, ready=ready),
                    patch.object(start, "processing_ready", return_value=ready),
                    patch.object(self.launcher, "command") as command,
                ):
                    if ready:
                        self.launcher.ensure_processing_service("wise", url, self.token)
                    else:
                        with self.assertRaisesRegex(start.StartupError, "不会改连本地"):
                            self.launcher.ensure_processing_service("wise", url, self.token)
                    command.assert_not_called()

    def test_redirects_do_not_forward_tokens(self):
        self.assertIsNone(
            start.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test")
        )

    def test_unsafe_urls_fail_before_authenticated_request(self):
        for url in (
            "http://user:password@127.0.0.1:9671",
            "http://127.0.0.1:9671?token=secret",
            "ftp://server",
        ):
            with (
                patch.object(start, "processing_ready") as ready,
                self.assertRaises(start.StartupError),
            ):
                self.launcher.ensure_processing_service("wise", url, self.token)
            ready.assert_not_called()

    def test_occupied_unverified_endpoint_never_changes_containers(self):
        with (
            patch.object(start, "processing_ready", return_value=False),
            patch.object(start, "port_open", return_value=True),
            patch.object(self.launcher, "command") as command,
        ):
            with self.assertRaisesRegex(start.StartupError, "未重启或替换"):
                self.launcher.ensure_processing_service("wise", "http://127.0.0.1:9671", self.token)
        command.assert_not_called()

    def test_healthy_wise_reused_without_docker_or_private_env(self):
        self.private.unlink()
        with (
            patch.object(start, "processing_ready", return_value=True),
            patch.object(self.launcher, "command") as command,
        ):
            self.launcher.ensure_processing_service("wise", "http://127.0.0.1:9671", self.token)
        command.assert_not_called()

    def test_missing_env_does_not_create_or_start_anything(self):
        self.fixture()
        self.private.unlink()
        with self.assertRaisesRegex(start.StartupError, "缺少私有"):
            self.launch()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.private.exists())

    def test_missing_image_never_builds_pulls_or_starts(self):
        self.fixture()
        self.missing_image = True
        with self.assertRaisesRegex(start.StartupError, "镜像尚未准备"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_local_wise_starts_only_prepared_service_and_preserves_env(self):
        self.fixture()
        original = self.private.read_bytes()
        self.launch()
        ups = [args for args, _ in self.calls if "up" in args]
        self.assertEqual(len(ups), 1)
        self.assertEqual(
            ups[0][ups[0].index("up") :],
            [
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--no-recreate",
                "wise",
            ],
        )
        self.assertEqual(self.private.read_bytes(), original)
        self.assertFalse(
            any(
                "build" in args or "pull" in args or "restart" in args or "down" in args
                for args, _ in self.calls
            )
        )

    def cuda_fixture(self, states=None):
        self.fixture(states=states)
        self.private.write_text("WISE_RUNTIME=cuda\n")
        (self.folder / "compose.wise.gpu.yml").write_text("# prepared fixture\n")
        wise = self.definition["services"]["wise"]
        wise["image"] = "inspiration-wise-bridge:fcfa443f-cu128"
        wise["deploy"] = {
            "resources": {
                "reservations": {
                    "devices": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]
                }
            }
        }

    def test_wise_runtime_literal_dotenv_and_environment_precedence(self):
        for content, env, expected in (
            ("# absent\n", {}, "cpu"),
            ("WISE_RUNTIME=\n", {}, "cpu"),
            ("WISE_RUNTIME= # default\n", {}, "cpu"),
            ("WISE_RUNTIME=''\n", {}, "cpu"),
            ("export WISE_RUNTIME = 'cuda' # explicit\n", {}, "cuda"),
            ('WISE_RUNTIME="cuda"\n', {}, "cuda"),
            ("WISE_RUNTIME=cuda # explicit\n", {}, "cuda"),
            ("WISE_RUNTIME=cpu\nWISE_RUNTIME=cuda\n", {}, "cuda"),
            ("WISE_RUNTIME=cuda\n", {"WISE_RUNTIME": "cpu"}, "cpu"),
            ("WISE_RUNTIME=cuda\n", {"WISE_RUNTIME": ""}, "cpu"),
            ("SECRET='multiline\nWISE_RUNTIME=cuda\nsecret'\n", {}, "cpu"),
        ):
            with self.subTest(content=content, env=env):
                self.private.write_text(content, encoding="utf-8")
                self.assertEqual(start.wise_runtime(self.private, env), expected)

    def test_invalid_wise_runtime_never_starts_or_leaks_value(self):
        for value in (
            "gpu",
            "CUDA",
            "cuda#comment",
            "${OTHER}",
            "'cudaX",
            "'cuda' extra",
        ):
            with self.subTest(value=value):
                self.fixture()
                self.private.write_text(f"WISE_RUNTIME={value}\n")
                with self.assertRaises(start.StartupError) as failure:
                    self.launch()
                self.assertNotIn(value, str(failure.exception))
                self.assertEqual(self.calls, [])
        self.private.write_text("WISE_RUNTIME=cuda\n")
        self.launcher.env["WISE_RUNTIME"] = "unknown-private-value"
        with self.assertRaisesRegex(start.StartupError, "仅支持"):
            self.launch()
        self.assertEqual(self.calls, [])

    def test_cuda_missing_override_does_not_fallback_or_start(self):
        self.fixture()
        self.private.write_text("WISE_RUNTIME=cuda\n")
        with self.assertRaisesRegex(start.StartupError, "缺少 compose.wise.gpu"):
            self.launch()
        self.assertEqual(self.calls, [])

    def test_empty_or_environment_cpu_keeps_original_compose(self):
        for env in ({}, {"WISE_RUNTIME": "cpu"}, {"WISE_RUNTIME": ""}):
            with self.subTest(env=env):
                self.calls.clear()
                self.rows.clear()
                self.fixture()
                self.private.write_text("WISE_RUNTIME=cuda\n" if env else "WISE_RUNTIME=\n")
                self.launcher.env.update(env)
                self.launch()
                compose = next(args for args, _ in self.calls if "config" in args)
                self.assertEqual(compose.count("-f"), 1)

    def test_stopped_cuda_resumes_with_override_and_existing_volume(self):
        self.cuda_fixture(states={"wise": "exited"})
        original = self.private.read_bytes()
        self.launch()
        for args, _ in self.calls:
            if args[1:2] == ["compose"]:
                self.assertIn(str(self.folder / "compose.wise.gpu.yml"), args)
                self.assertLess(
                    args.index(str(self.folder / "compose.wise.yml")),
                    args.index(str(self.folder / "compose.wise.gpu.yml")),
                )
        up = next(args for args, _ in self.calls if "up" in args)
        self.assertEqual(up[-1], "wise")
        self.assertIn("--no-recreate", up)
        self.assertEqual(self.private.read_bytes(), original)

    def test_stopped_cuda_requires_matching_gpu_image_and_volume(self):
        for mismatch in ("gpu", "image", "volume", "compose-gpu"):
            with self.subTest(mismatch=mismatch):
                self.calls.clear()
                self.rows.clear()
                self.stale_image = mismatch == "image"
                self.stale_volume = mismatch == "volume"
                self.device_requests = [] if mismatch == "gpu" else None
                self.cuda_fixture(states={"wise": "exited"})
                if mismatch == "compose-gpu":
                    self.definition["services"]["wise"]["deploy"] = {}
                with self.assertRaises(start.StartupError):
                    self.launch()
                self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_cpu_selection_does_not_resume_gpu_request_container(self):
        self.fixture(states={"wise": "exited"})
        self.device_requests = [{"Driver": "nvidia", "Count": 1, "Capabilities": [["gpu"]]}]
        with self.assertRaisesRegex(start.StartupError, "GPU 申请"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_healthy_wise_never_switches_to_new_runtime_choice(self):
        self.fixture(states={"wise": "running"})
        self.private.write_text("WISE_RUNTIME=cuda\n")
        # Even a missing override must not disturb an already authenticated service.
        with (
            patch.object(self.launcher, "command") as command,
            patch.object(start, "processing_ready", return_value=True),
        ):
            self.launcher.ensure_processing_service("wise", self.url, self.token)
        command.assert_not_called()

    def test_config_port_token_and_offline_must_match_before_start(self):
        cases = ("port", "token", "offline", "face")
        for case in cases:
            with self.subTest(case=case):
                self.calls.clear()
                self.fixture()
                service = self.definition["services"]["wise"]
                if case == "port":
                    service["ports"][0]["published"] = "9672"
                elif case == "token":
                    service["environment"]["WISE_BRIDGE_TOKEN"] = "another-token"
                elif case == "face":
                    service["environment"]["WISE_BRIDGE_CAPABILITIES"] = "visual,face"
                else:
                    service["environment"]["HF_HUB_OFFLINE"] = "0"
                with self.assertRaises(start.StartupError):
                    self.launch()
                self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_optional_wise_resumes_only_existing_exact_container(self):
        for capabilities in ("visual,audio,object", "visual,face"):
            with self.subTest(capabilities=capabilities):
                self.calls.clear()
                self.rows.clear()
                self.cuda_fixture(states={"wise": "exited"})
                self.definition["services"]["wise"]["environment"]["WISE_BRIDGE_CAPABILITIES"] = (
                    capabilities
                )
                original = self.private.read_bytes()
                self.launch()
                starts = [args for args, _ in self.calls if args[1:2] == ["start"]]
                self.assertEqual(
                    starts,
                    [["docker", "start", start.hashlib.sha256(b"wise").hexdigest()]],
                )
                self.assertFalse(
                    any(
                        "up" in args or args[1:2] in (["run"], ["pull"], ["build"], ["restart"])
                        for args, _ in self.calls
                    )
                )
                self.assertEqual(original, self.private.read_bytes())

    def test_optional_wise_missing_created_or_paused_never_auto_deploys(self):
        for state in (None, "created", "paused", "restarting"):
            with self.subTest(state=state):
                self.calls.clear()
                self.rows.clear()
                self.fixture(states={"wise": state} if state else {})
                self.definition["services"]["wise"]["environment"]["WISE_BRIDGE_CAPABILITIES"] = (
                    "visual,object"
                )
                with self.assertRaisesRegex(start.StartupError, "既有已停止"):
                    self.launch()
                self.assertFalse(
                    any("up" in args or args[1:2] == ["start"] for args, _ in self.calls)
                )

    def test_optional_wise_rejects_configuration_and_fixed_identity_drift(self):
        for mismatch in (
            "env",
            "capabilities",
            "image",
            "image_id",
            "volume",
            "gpu",
            "offline",
            "token",
        ):
            with self.subTest(mismatch=mismatch):
                self.calls.clear()
                self.rows.clear()
                self.stale_env = mismatch == "env"
                self.stale_capabilities = mismatch == "capabilities"
                self.stale_image = mismatch == "image"
                self.stale_image_id = mismatch == "image_id"
                self.stale_volume = mismatch == "volume"
                self.device_requests = [] if mismatch == "gpu" else None
                self.cuda_fixture(states={"wise": "exited"})
                env = self.definition["services"]["wise"]["environment"]
                env["WISE_BRIDGE_CAPABILITIES"] = "visual,audio,object"
                if mismatch == "offline":
                    env["TRANSFORMERS_OFFLINE"] = "0"
                if mismatch == "token":
                    env["WISE_BRIDGE_TOKEN"] = "changed-compose-token"
                with self.assertRaises(start.StartupError):
                    self.launch()
                self.assertFalse(
                    any("up" in args or args[1:2] == ["start"] for args, _ in self.calls)
                )

    def test_optional_container_disappearing_after_inspection_is_not_recreated(self):
        self.fixture(states={"wise": "exited"})
        self.definition["services"]["wise"]["environment"]["WISE_BRIDGE_CAPABILITIES"] = (
            "visual,object"
        )
        self.start_missing = True
        with self.assertRaisesRegex(start.StartupError, "不会创建替代"):
            self.launch()
        self.assertTrue(any(args[1:2] == ["start"] for args, _ in self.calls))
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_stopped_container_with_stale_env_not_resumed_or_recreated(self):
        self.fixture(states={"wise": "exited"})
        self.stale_env = True
        with self.assertRaisesRegex(start.StartupError, "配置不一致"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_stopped_worker_with_other_cache_volume_is_not_resumed(self):
        self.fixture("mediacms", states={"worker": "exited"}, speech=True)
        self.stale_volume = True
        with self.assertRaisesRegex(start.StartupError, "数据卷与 Compose 不一致"):
            self.launch()
        self.assertFalse(any("up" in args or args[1:2] == ["run"] for args, _ in self.calls))

    def test_paused_container_is_not_restarted(self):
        self.fixture(states={"wise": "paused"})
        with self.assertRaisesRegex(start.StartupError, "不会干预"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_optional_failure_does_not_block_other_processing_service_or_leak_body(
        self,
    ):
        config = {
            "library_processing_enabled": True,
            "library_wise_url": "http://127.0.0.1:9671",
            "library_wise_token": self.token,
            "library_mediacms_url": "http://127.0.0.1:18080",
            "library_mediacms_token": self.token,
        }
        with (
            patch.object(
                self.launcher,
                "ensure_processing_service",
                side_effect=[ValueError(self.token), None],
            ) as ensure,
            patch.object(start, "say") as say,
        ):
            self.launcher.processing_services(config)
        self.assertEqual(ensure.call_count, 2)
        self.assertNotIn(self.token, str(say.call_args_list))

    def test_cms_never_reruns_initialization(self):
        self.fixture("mediacms")
        self.rows.pop("init")
        with self.assertRaisesRegex(start.StartupError, "初始化记录"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_healthy_cms_recovers_only_missing_worker_without_touching_web(self):
        self.fixture("mediacms", states={s: "running" for s in ("db", "redis", "web", "beat")})
        self.launch(ready=True)
        ups = [args for args, _ in self.calls if "up" in args]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0][ups[0].index("--no-recreate") + 1 :], ["worker"])
        self.assertFalse(any("init" in args for args, _ in self.calls))

    def test_cms_start_orders_dependencies_without_reinitializing(self):
        self.fixture("mediacms")
        self.launch()
        ups = [args[args.index("--no-recreate") + 1 :] for args, _ in self.calls if "up" in args]
        self.assertEqual(ups, [["db", "redis"], ["web", "worker", "beat"]])

    def test_cms_whisper_cache_is_verified_offline_before_starting_worker(self):
        self.fixture("mediacms", speech=True)
        self.launch()
        probe = next(args for args, _ in self.calls if args[1:2] == ["run"])
        self.assertEqual(probe[probe.index("--network") + 1], "none")
        self.assertIn("--read-only", probe)
        self.assertIn(
            "type=volume,source=inspiration-media-services_cache,target=/cache,readonly",
            probe,
        )
        self.assertIn(start.WHISPER_CACHE_CHECK, probe)
        self.assertNotIn("import whisper", start.WHISPER_CACHE_CHECK)

    def test_missing_whisper_prevents_all_stack_starts(self):
        self.fixture("mediacms", speech=True)
        self.cache_ok = False
        with self.assertRaisesRegex(start.StartupError, "Whisper 缓存缺失"):
            self.launch()
        self.assertFalse(any("up" in args for args, _ in self.calls))

    def test_authenticated_health_uses_exact_identity_without_logging_token(self):
        data = {
            "service": "inspiration-wise-bridge",
            "upstream_commit": start.WISE_COMMIT,
            "worker_alive": True,
            "upstream_installation_verified": True,
        }
        for change, expected in (
            ({}, True),
            ({"service": "other"}, False),
            ({"upstream_commit": "old"}, False),
            ({"worker_alive": False}, False),
        ):
            with patch.object(
                start.PROCESSING_HTTP,
                "open",
                return_value=io.BytesIO(json.dumps({**data, **change}).encode()),
            ) as opened:
                self.assertEqual(
                    start.processing_ready("wise", "http://127.0.0.1:9671", self.token),
                    expected,
                )
                request = opened.call_args.args[0]
                self.assertEqual(request.get_header("Authorization"), "Bearer " + self.token)
                self.assertNotIn(self.token, request.full_url)

    def test_cms_checks_protected_bridge_and_paginated_media_endpoint(self):
        responses = [
            {
                "name": "Upload",
                "renders": ["application/json"],
                "parses": ["multipart/form-data"],
            },
            {"count": 0, "results": []},
        ]
        with patch.object(
            start.PROCESSING_HTTP,
            "open",
            side_effect=[io.BytesIO(json.dumps(data).encode()) for data in responses],
        ) as opened:
            self.assertTrue(
                start.processing_ready("mediacms", "http://127.0.0.1:18080", self.token)
            )
        requests = [call.args[0] for call in opened.call_args_list]
        self.assertEqual([request.method for request in requests], ["OPTIONS", "GET"])
        self.assertTrue(requests[0].full_url.endswith("/api/inspiration/v1/media/"))
        self.assertTrue(
            all(
                request.get_header("Authorization") == "Token " + self.token for request in requests
            )
        )
        with patch.object(
            start.PROCESSING_HTTP, "open", return_value=io.BytesIO(b'{"status":"ok"}')
        ):
            self.assertFalse(
                start.processing_ready("mediacms", "http://127.0.0.1:18080", self.token)
            )

    def test_cold_wise_worker_is_reused_without_claiming_installation_verified(self):
        health = {
            "service": "inspiration-wise-bridge",
            "upstream_commit": start.WISE_COMMIT,
            "worker_alive": True,
            "upstream_installation_verified": False,
            "models_verified": False,
        }
        with (
            patch.object(
                start.PROCESSING_HTTP,
                "open",
                return_value=io.BytesIO(json.dumps(health).encode()),
            ) as opened,
            patch.object(self.launcher, "command") as command,
            patch.object(start, "wait_for") as wait,
            patch.object(start, "say") as say,
        ):
            self.launcher.ensure_processing_service("wise", "http://127.0.0.1:9671", self.token)
        opened.assert_called_once()
        command.assert_not_called()
        wait.assert_not_called()
        self.assertIn("上游安装由实际任务检查", str(say.call_args_list))
        self.assertFalse(health["upstream_installation_verified"])
        self.assertNotIn(self.token, str(say.call_args_list))


if __name__ == "__main__":
    unittest.main()
