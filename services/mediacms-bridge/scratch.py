"""Confined per-attempt scratch space for a runner holding its job lease."""

import json
import os
import shutil
import signal
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def process_info(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": int(pid), "state": fields[0], "group": int(fields[2]), "start": fields[19]}
    except FileNotFoundError:
        return None


def group_members(group):
    members = []
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            member = process_info(int(path.name))
            if member and member["group"] == group and member["state"] not in ("Z", "X"):
                members.append(member)
    return members


def stop_children():
    """Reap only processes in this isolated runner's own dedicated group."""
    own = os.getpid()
    if os.getpgrp() != own:
        raise RuntimeError("Scratch cleanup requires a dedicated processing group")
    for _ in range(50):
        children = [item for item in group_members(own) if item["pid"] != own]
        if not children:
            return
        for child in children:
            # Do not signal a PID that exited and was reused since enumeration.
            if process_info(child["pid"]) == child:
                try:
                    os.kill(child["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(0.1)
    raise RuntimeError("Processing children have not stopped; scratch files were retained")


def identity():
    current = process_info(os.getpid())
    return {
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "namespace": os.readlink("/proc/self/ns/pid"),
        "pid": os.getpid(),
        "group": os.getpgrp(),
        "start": current["start"],
    }


def known_stopped(owner, current):
    if not isinstance(owner, dict) or set(owner) != set(current):
        return False
    if not all(
        isinstance(owner[key], str) and owner[key] for key in ("boot", "namespace", "start")
    ):
        return False
    if not isinstance(owner["pid"], int) or owner["pid"] <= 1 or owner["group"] != owner["pid"]:
        return False
    if owner["boot"] != current["boot"]:
        return True  # this host has rebooted since the attempt
    if owner["namespace"] != current["namespace"]:
        # A different container namespace is not evidence that its processes died.
        # Keep ambiguous leftovers for the operator's offline disk maintenance.
        return False
    previous = process_info(owner["pid"])
    if previous and previous["start"] == owner["start"] and previous["state"] not in ("Z", "X"):
        return False
    return not group_members(owner["group"])


def remove_attempt(path, job):
    if path.is_symlink() or not path.is_dir() or path.parent.resolve() != job:
        raise RuntimeError("Untrusted processing scratch path")
    if path.resolve().parent != job or len(path.name) != 32:
        raise RuntimeError("Untrusted processing scratch path")
    try:
        int(path.name, 16)
    except ValueError as exc:
        raise RuntimeError("Untrusted processing scratch path") from exc
    shutil.rmtree(path)


@contextmanager
def job_scratch(settings, kind, identifier):
    """Call only while the raw exclusive job lease is held, in a private runner."""
    if (
        kind not in ("initialize", "encode", "speech")
        or not isinstance(identifier, int)
        or identifier < 1
    ):
        raise ValueError("Invalid scratch job identity")
    root = Path(settings.TEMP_DIRECTORY).resolve(strict=True)
    jobs = root / "inspiration-jobs"
    jobs.mkdir(exist_ok=True)
    job = jobs / f"{kind}-{identifier}"
    if jobs.is_symlink() or jobs.resolve().parent != root:
        raise RuntimeError("Untrusted processing scratch root")
    job.mkdir(exist_ok=True)
    if job.is_symlink() or job.resolve().parent != jobs:
        raise RuntimeError("Untrusted processing scratch job")
    current = identity()
    for previous in job.iterdir():
        marker = previous / "owner.json"
        if previous.is_symlink() or not previous.is_dir() or marker.is_symlink():
            continue
        try:
            if marker.stat().st_size > 2048:
                continue
            owner = json.loads(marker.read_text())
        except (OSError, ValueError):
            continue
        if known_stopped(owner, current):
            remove_attempt(previous, job)
    attempt = job / uuid.uuid4().hex
    attempt.mkdir(mode=0o700)
    (attempt / "owner.json").write_text(json.dumps(current))
    before = settings.TEMP_DIRECTORY, tempfile.tempdir
    environment = {name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP")}
    settings.TEMP_DIRECTORY = tempfile.tempdir = str(attempt)
    os.environ.update({name: str(attempt) for name in environment})
    try:
        yield attempt
    finally:
        # Keep the lease while checking/killing only this attempt's child group.
        # On hard SIGKILL the marker survives and the next attempt checks liveness.
        stop_children()
        settings.TEMP_DIRECTORY, tempfile.tempdir = before
        for name, value in environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        remove_attempt(attempt, job)
        if not any(job.iterdir()):
            job.rmdir()
