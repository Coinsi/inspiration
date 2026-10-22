import unittest
from pathlib import Path
from unittest.mock import patch

import launcher_services as lifecycle


class LifecycleTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def process(
        self, pid=10, parent=1, exe=None, command=None, started="2026-01-01T00:00:00Z"
    ):
        python = str(self.root / "backend/.venv/Scripts/python.exe")
        return {
            "ProcessId": pid,
            "ParentProcessId": parent,
            "ExecutablePath": python if exe is None else exe,
            "CommandLine": f'"{python}" -m uvicorn app.main:app --port 8000'
            if command is None
            else command,
            "Started": started,
        }

    def test_exact_checkout_and_module_required(self):
        mine = self.process()
        other = self.process(
            pid=11, exe=str(self.root / "other/backend/.venv/Scripts/python.exe")
        )
        unrelated = self.process(pid=12, command='python -c "print(123)"')
        unreadable = self.process(pid=13, exe="", command="")
        owned = lifecycle.owned_processes(
            self.root, [mine, other, unrelated, unreadable]
        )
        self.assertEqual(set(owned), {10})

    def test_vite_argument_boundary_and_descendants(self):
        vite = str(self.root / "frontend/node_modules/vite/bin/vite.js")
        mine = self.process(
            command=f'node "{vite}" --port 15173', exe="C:\\node\\node.exe"
        )
        other = self.process(
            pid=20, command=f'node "{vite}.backup"', exe="C:\\node\\node.exe"
        )
        child = self.process(pid=11, parent=10, exe="C:\\Python\\python.exe")
        reused_parent = self.process(
            pid=12,
            parent=10,
            exe="C:\\Python\\python.exe",
            started="2025-01-01T00:00:00Z",
        )
        self.assertEqual(
            set(
                lifecycle.owned_processes(
                    self.root, [mine, other, child, reused_parent]
                )
            ),
            {10, 11},
        )

    def test_unverified_listener_blocks_before_any_stop(self):
        state = {
            "processes": [self.process()],
            "listeners": [{"LocalPort": 8000, "OwningProcess": 99}],
        }
        with (
            patch.object(lifecycle, "snapshot", return_value=state),
            patch.object(lifecycle, "powershell") as stop,
        ):
            with self.assertRaisesRegex(RuntimeError, "未停止任何服务"):
                lifecycle.manage(self.root, "stop", lambda _: None)
            stop.assert_not_called()

    def test_status_never_terminates(self):
        with (
            patch.object(
                lifecycle,
                "snapshot",
                return_value={"processes": [self.process()], "listeners": []},
            ),
            patch.object(lifecycle, "powershell") as stop,
        ):
            lifecycle.manage(self.root, "status", lambda _: None)
            stop.assert_not_called()

    def test_access_checked_before_stop_and_children_first(self):
        parent, child = self.process(), self.process(pid=11, parent=10)
        state = {
            "processes": [parent, child],
            "listeners": [{"LocalPort": 8000, "OwningProcess": 11}],
        }
        with (
            patch.object(
                lifecycle,
                "snapshot",
                side_effect=[state, {"processes": [], "listeners": []}],
            ),
            patch.object(lifecycle, "check_termination_access") as access,
            patch.object(lifecycle, "powershell") as stop,
        ):
            events = []
            lifecycle.manage(self.root, "stop", events.append)
            access.assert_called_once()
            self.assertEqual(stop.call_count, 2)
            self.assertIn("CreationDate", stop.call_args.args[0])
            stopped = [e for e in events if e.startswith("[停止] 项目进程")]
            self.assertEqual(
                stopped, ["[停止] 项目进程 PID 11", "[停止] 项目进程 PID 10"]
            )

    def test_permission_failure_leaves_everything_running(self):
        state = {"processes": [self.process()], "listeners": []}
        with (
            patch.object(lifecycle, "snapshot", return_value=state),
            patch.object(
                lifecycle,
                "check_termination_access",
                side_effect=RuntimeError("权限不足"),
            ),
            patch.object(lifecycle, "powershell") as stop,
        ):
            with self.assertRaisesRegex(RuntimeError, "权限不足"):
                lifecycle.manage(self.root, "stop", lambda _: None)
            stop.assert_not_called()
