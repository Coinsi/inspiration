"""Guarded observation/control helpers for explicitly scheduled WISE fault tests."""

import inspect
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

from app.core.config import settings


def require_window(environment, container_id=None, *, now=None):
    now = now or datetime.now(UTC)
    assert environment.get("INSPIRATION_WISE_EXCLUSIVE_ACK") == "non-test-work-is-idle", (
        "Operator must explicitly reserve an exclusive idle window"
    )
    try:
        until = datetime.fromisoformat(environment["INSPIRATION_WISE_EXCLUSIVE_UNTIL"])
    except (KeyError, ValueError):
        raise AssertionError("Set an explicit timezone-aware exclusive-window expiry") from None
    assert until.tzinfo is not None and now < until <= now + timedelta(hours=2), (
        "Exclusive window must be current and at most two hours long"
    )
    if container_id is not None:
        assert re.fullmatch(r"[0-9a-f]{64}", container_id)
        assert environment.get("INSPIRATION_WISE_FAULT_CONTAINER_ID") == container_id, (
            "Container hard kill requires this exact full container ID to be authorized"
        )


def assert_idle(observation, main, cms, *, allowed_project=None):
    assert all(job["project_id"] == allowed_project for job in observation["active_jobs"]), (
        "Another WISE project has queued or running work"
    )
    assert main and all(count == 0 for count in main.values()), "Main application work is active"
    assert cms and all(count == 0 for count in cms.values()), "MediaCMS work or copies remain"


def readonly_observation(data_dir, project_id):
    """Runs unchanged in the container; only reads SQLite, owned paths and /proc."""
    import os
    import sqlite3
    import uuid
    from pathlib import Path

    root = Path(data_dir).resolve()
    project_id = str(uuid.UUID(project_id))
    with sqlite3.connect((root / "bridge.sqlite3").as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        active = [
            dict(row)
            for row in conn.execute(
                "SELECT id,project_id,source_id,state,phase,attempts FROM jobs "
                "WHERE state IN ('queued','processing')"
            )
        ]
        jobs = [
            dict(row)
            for row in conn.execute(
                "SELECT id,source_id,state,phase,attempts,kind FROM jobs WHERE project_id=?",
                (project_id,),
            )
        ]
        sources = [
            dict(row)
            for row in conn.execute(
                "SELECT id,active,deleted,generation FROM sources WHERE project_id=?",
                (project_id,),
            )
        ]
        head = conn.execute("SELECT revision FROM projects WHERE id=?", (project_id,)).fetchone()
        snapshots = [
            dict(row)
            for row in conn.execute(
                "SELECT revision,job_id,published FROM snapshots WHERE project_id=?",
                (project_id,),
            )
        ]
    processes = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(path.name)
            fields = (path / "stat").read_text().rpartition(") ")[2].split()
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if pid != os.getpid() and fields[0] != "Z":
                processes.append(
                    {
                        "pid": pid,
                        "ppid": int(fields[1]),
                        "pgid": int(fields[2]),
                        "start_ticks": fields[19],
                        "owned": project_id in command,
                    }
                )
        except (OSError, ValueError, IndexError):
            continue  # a process may exit during a read-only observation
    owned_groups = {row["pgid"] for row in processes if row["owned"]}
    source_dir = root / "sources" / project_id
    snapshot_dir = root / "projects" / project_id / "snapshots"
    files = [
        {"name": path.name, "bytes": path.stat().st_size}
        for path in source_dir.glob("*")
        if path.is_file()
    ]
    return {
        "head": head[0] if head else None,
        "active_jobs": active,
        "jobs": jobs,
        "sources": sources,
        "snapshots": snapshots,
        "source_files": files,
        "snapshot_directories": sorted(
            path.name for path in snapshot_dir.glob("*") if path.is_dir()
        ),
        "owned_processes": [row for row in processes if row["pgid"] in owned_groups],
        "live_groups": sorted({row["pgid"] for row in processes}),
    }


def main_idle_counts():
    # This separate, untranslated connection observes the real application schema;
    # the disposable test schema/session is never mistaken for the global queue.
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            return {
                "processing": connection.scalar(
                    text(
                        "SELECT count(*) FROM media_processing_run "
                        "WHERE status IN ('queued','processing','waiting')"
                    )
                ),
                "uploads": connection.scalar(
                    text(
                        "SELECT count(*) FROM media_version "
                        "WHERE status IN ('uploading','queued','processing')"
                    )
                ),
                "generation": connection.scalar(
                    text(
                        "SELECT count(*) FROM generation_job "
                        "WHERE status IN ('pending','submitted','running')"
                    )
                ),
            }
    finally:
        engine.dispose()


CMS_IDLE_SCRIPT = """
import os,json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','cms.settings')
import django
django.setup()
from files.models import Media,Encoding,TranscriptionRequest
from celery import current_app
result={'media':Media.objects.count(),
 'encodings':Encoding.objects.filter(status__in=('pending','running')).count(),
 'speech':TranscriptionRequest.objects.filter(status__in=('pending','running')).count()}
for kind in ('active','reserved','scheduled'):
    response=getattr(current_app.control.inspect(timeout=3),kind)()
    assert isinstance(response,dict) and response,'Cannot prove Celery is idle'
    result[kind]=sum(len(rows) for rows in response.values())
print(json.dumps(result))
"""


class ContainerAccess:
    def __init__(self, container, cms_container):
        self.container = container
        self.cms_container = cms_container
        initial = self.inspect()
        assert initial["running"] and initial["service"] == "wise"
        self.container = initial["id"]  # all later operations use the pinned container ID
        self.initial = initial
        self.python = os.environ.get(
            "INSPIRATION_WISE_CONTAINER_PYTHON", "/opt/conda/envs/wise-env/bin/python"
        )

    @staticmethod
    def command(arguments, *, timeout=35):
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        # Never print Docker environment, provider tokens or Django diagnostics.
        assert result.returncode == 0, "Controlled container command failed; inspect private logs"
        return result.stdout.strip()

    def inspect(self):
        template = (
            '{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},'
            '"pid":{{json .State.Pid}},"started":{{json .State.StartedAt}},'
            '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
        )
        return json.loads(self.command(["docker", "inspect", "--format", template, self.container]))

    def observe(self, project_id):
        script = (
            "import os,sys,json\n"
            + inspect.getsource(readonly_observation)
            + "\nprint(json.dumps(readonly_observation("
            "os.environ.get('WISE_BRIDGE_DATA','/data'),sys.argv[1])))"
        )
        return json.loads(
            self.command(
                ["docker", "exec", self.container, self.python, "-c", script, str(project_id)]
            )
        )

    def cms_idle(self):
        return json.loads(
            self.command(["docker", "exec", self.cms_container, "python", "-c", CMS_IDLE_SCRIPT])
        )

    def require_idle(self, project_id):
        require_window(os.environ)
        observation = self.observe(project_id)
        assert_idle(observation, main_idle_counts(), self.cms_idle(), allowed_project=project_id)
        return observation

    def kill_and_restore(self, project_id, job_id):
        self.require_idle(project_id)
        require_window(os.environ, self.container)
        before = self.inspect()
        assert before["running"] and before["image"] == self.initial["image"]
        # Idle probes can take seconds. Recheck the *actual live target* last,
        # rather than claiming an already finished job was killed in flight.
        target = self.observe(project_id)
        current = next(row for row in target["jobs"] if row["id"] == job_id)
        assert current["state"] == "processing" and current["phase"] == "extracting"
        assert target["owned_processes"], "No owned WISE extraction child remains"
        # Only the exact opted-in existing WISE container is touched. No compose
        # up/down, rebuild, image pull, volume reset or broad process killing.
        try:
            self.command(["docker", "kill", "--signal", "KILL", self.container])
            interrupted = self.inspect()
            assert not interrupted["running"] or interrupted["started"] != before["started"]
        finally:
            # A test assertion/deadline must not leave a container that this test
            # stopped offline. Restore that exact identity, never a replacement.
            if not self.inspect()["running"]:
                self.command(["docker", "start", self.container], timeout=60)
        after = self.inspect()
        assert after["id"] == before["id"] and after["image"] == before["image"]
        assert after["started"] != before["started"]
        return {
            "before": before,
            "target_at_interrupt": target,
            "after_interrupt": interrupted,
            "after_restart": after,
        }
