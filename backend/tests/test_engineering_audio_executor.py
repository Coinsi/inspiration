"""Actual FFmpeg audio samples plus the persistent engineering preview path."""

# ruff: noqa: F811
import array
import hashlib
import io
import uuid
import wave

import pytest

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import audio_executor, documents, executors
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def constant_audio():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(array.array("h", [10000] * 48000).tobytes())
    return stream.getvalue()


def payload():
    return {
        "sample_rate": 48000,
        "channels": 1,
        "tracks": [
            {
                "id": str(uuid.uuid4()),
                "name": "对白",
                "gain_db": -6,
                "clips": [
                    {
                        "id": str(uuid.uuid4()),
                        "source": "voice",
                        "start": {"numerator": 1, "denominator": 2},
                        "source_range": {
                            "start": {"numerator": 1, "denominator": 4},
                            "end": {"numerator": 3, "denominator": 4},
                        },
                    }
                ],
            }
        ],
    }


def test_sample_placement_trim_gain_and_temporary_cleanup(tmp_path):
    raw = constant_audio()
    file = tmp_path / "voice.wav"
    file.write_bytes(raw)
    child, root = str(uuid.uuid4()), str(uuid.uuid4())
    nodes = [
        {
            "id": child,
            "revision": 1,
            "kind": "resource",
            "schema_version": 1,
            "inputs": {},
            "payload": {"media_type": "audio", "blob_hash": hashlib.sha256(raw).hexdigest()},
        },
        {
            "id": root,
            "revision": 1,
            "kind": "audio.document",
            "schema_version": 1,
            "inputs": {"voice": {"target": {"id": child, "revision": 1}, "accepts": "resource"}},
            "payload": payload(),
        },
    ]
    for node in nodes:
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    body = {
        "executor": audio_executor.EXECUTOR,
        "root": {"id": root, "revision": 1},
        "nodes": nodes,
    }
    plan = {**body, "fingerprint": documents.digest({}, body)}
    with executors.execute(plan, lambda _: raw, lambda _: file, lambda: False) as (output, typ):
        assert typ == "audio"
        with wave.open(str(output), "rb") as wav:
            assert wav.getframerate() == 48000
            assert wav.getnframes() == 48000
            samples = array.array("h", wav.readframes(48000))
        assert max(abs(v) for v in samples[:24000]) == 0
        assert 5000 < max(samples[24000:]) < 5025
    assert not output.exists()
    # An overrun must fail, not silently pad the missing source material.
    nodes[-1]["payload"]["tracks"][0]["clips"][0]["source_range"]["end"] = {
        "numerator": 2,
        "denominator": 1,
    }
    nodes[-1]["content_hash"] = documents.digest(nodes[-1]["inputs"], nodes[-1]["payload"])
    plan = {**body, "fingerprint": documents.digest({}, body)}
    with pytest.raises(CapabilityUnsupported):
        with executors.execute(plan, lambda _: raw, lambda _: file, lambda: False):
            pytest.fail("A short source must not yield an output")


@pytest.mark.parametrize("version", [1, 2])
def test_audio_document_persistent_preview_has_correct_mime(studio, version):
    from app.models.generation import Generation, GenerationJob
    from app.models.storage import Blob
    from app.storage import cas
    from tests.test_engineering import accept
    from tests.test_engineering_review import preview, ready

    client, sessions, ids, path, tid, _ = ready(studio)
    with sessions() as db:
        blob = cas.put_bytes(db, constant_audio(), "audio/wav", duration_ms=1000)
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="timeline",
            target_id=uuid.UUID(tid),
            provider="local",
            status="succeeded",
        )
        db.add(job)
        db.flush()
        generation = Generation(
            project_id=job.project_id,
            job_id=job.id,
            target_type="timeline",
            target_id=job.target_id,
            provider="local",
            output_type="audio",
            output_blob_hash=blob.hash,
        )
        db.add(generation)
        db.flush()
        source = str(generation.id)
        db.commit()
    usage = str(uuid.uuid4())
    source_change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "resource.add",
                    "usage_id": usage,
                    "source_kind": "generation",
                    "source_id": source,
                    "name": "对白",
                }
            ],
        },
    )
    accept(client, path, source_change)
    content = payload()
    if version == 2:
        from app.modules.engineering import native_v2

        _, content = native_v2.migrate("audio.document", {}, content)
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "audio.document",
                    "title": "音频工程",
                    "schema_version": version,
                    "payload": content,
                    "inputs": {
                        "voice": {
                            "target": source_change["snapshot"]["resource_roots"][usage],
                            "accepts": "resource",
                        }
                    },
                }
            ],
        },
    )
    result = preview(client, path, change)
    assert result["preview"]["status"] == "succeeded", result
    assert result["preview"]["output_type"] == "audio"
    with sessions() as db:
        assert db.get(Blob, result["preview"]["blob_hash"]).mime == "audio/wav"
    assert client.get(path).json()["revision"] == 1
