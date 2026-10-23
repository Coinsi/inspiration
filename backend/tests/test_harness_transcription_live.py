"""Opt-in local speech acceptance; never runs in ordinary or cloud-model CI.

HARNESS_REAL_ASR=1 invokes only http://127.0.0.1:8012. Input speech is created by
installed Windows SAPI, not a paid TTS service. PostgreSQL state uses the existing
isolated studio schema, which is dropped afterward. No user projects are edited.
"""

# ruff: noqa: F811
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.harness import ProductionRun, ProductionTask
from app.models.transcription import TranscriptionRun
from app.modules.generation import video_tools
from app.modules.harness import runtime, service, transcription_tools
from app.modules.tools import capabilities, registry
from app.modules.transcription import worker
from tests.test_harness_editing_tools import source
from tests.test_harness_runtime import plan, registered_fixture_kernel, seeded  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESS_REAL_ASR") != "1", reason="Explicit local ASR opt-in required"
)
PHRASE = "This is a local production test. The music is quiet. Please keep the picture unchanged."


def test_actual_windows_speech_through_local_asr_and_coordinator(studio, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    monkeypatch.setattr(settings, "transcriber_url", "http://127.0.0.1:8012")
    monkeypatch.setattr(settings, "harness_transcription_cost_mode", "self_hosted")
    monkeypatch.setattr(settings, "transcription_worker_enabled", True)
    transcription_tools.register_tools()
    _, sessions, ids = studio
    wav = tmp_path / "local-production-speech.wav"
    script = """
$ErrorActionPreference = 'Stop'
$voice = New-Object -ComObject SAPI.SpVoice
$english = $voice.GetVoices('Language=409')
if ($english.Count -eq 0) { throw 'No installed English SAPI voice' }
$voice.Voice = $english.Item(0)
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Open('__AUDIO_PATH__', 3, $false)
try {
  $voice.AudioOutputStream = $stream
  $null = $voice.Speak('__PHRASE__')
  $voice.Voice.GetDescription()
} finally { $stream.Close() }
""".replace("__AUDIO_PATH__", str(wav).replace("'", "''")).replace(
        "__PHRASE__", PHRASE.replace("'", "''")
    )
    synth = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )
    duration = video_tools.probe_file(wav)["duration_ms"]
    ref = source(sessions, ids, wav.read_bytes(), "audio", library=True, duration_ms=duration)
    _, _, rid = seeded(
        studio,
        plan(
            (
                "transcribe",
                {
                    "kind": "tool",
                    "tool": "media.transcribe",
                    "expected_output": "observation",
                    "arguments": {
                        "version_id": ref["id"],
                        "expected_blob_hash": ref["expected_blob_hash"],
                        "language": "en",
                    },
                },
            )
        ),
        effects=["read", "candidate"],
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert task.status == "waiting_transcription", task.error
        ident = uuid.UUID(task.state["waiting_transcription_id"])
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    started = time.monotonic()
    worker.process(ident)
    elapsed = time.monotonic() - started
    with sessions() as db:
        row = db.get(TranscriptionRun, ident)
        assert row.status == "ready", row.error
        assert row.cues and row.completed == row.total
        assert row.published_revision is None
        assert all(0 <= cue["start_ms"] < cue["end_ms"] <= duration for cue in row.cues)
        assert service.claim(db, rid) is None
        db.commit()
        assert db.get(ProductionRun, rid).status == "succeeded"
        evidence = {
            "recognizer": row.model_key,
            "protocol_simulated": False,
            "external_paid_provider": False,
            "speech_source": synth.stdout.strip(),
            "source_text": PHRASE,
            "source_duration_ms": duration,
            "worker_elapsed_seconds": round(elapsed, 3),
            "cues": row.cues,
            "published_to_library": False,
            "coordinator_status": "succeeded",
            "coverage": (
                "One synthetic English phrase through real local ASR; "
                "not a quality benchmark or word alignment proof"
            ),
        }
    folder = os.environ.get("HARNESS_LIVE_ASR_EVIDENCE")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        (target / wav.name).write_bytes(wav.read_bytes())
        evidence["source_audio"] = str(target / wav.name)
        (target / "local-asr-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    recognized = " ".join(cue["text"] for cue in evidence["cues"])
    assert "local" in recognized.lower() and "music" in recognized.lower(), recognized
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
