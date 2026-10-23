"""Actual decode/stream acceptance and durable restart-safe validation receipts."""

# ruff: noqa: F811
import copy
import io
import json
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.errors import Forbidden
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.harness import technical_validation as validation
from app.storage import cas
from tests.test_harness_editing_tools import ctx, png, sound, source
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(validation, "_CACHE", OrderedDict())


def video(path, *, audio=True):
    args = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=24:duration=1"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-shortest"]
    media_engine.run(
        [*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    )
    return path.read_bytes()


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
def test_real_full_validation_and_persistent_cache_across_restart(
    studio, tmp_path, monkeypatch, kind
):
    _, sessions, ids = studio
    data = (
        png("red")
        if kind == "image"
        else sound(1)
        if kind == "audio"
        else video(tmp_path / "clip.mp4")
    )
    ref = source(sessions, ids, data, kind, duration_ms=1000 if kind != "image" else None)
    with sessions() as db:
        receipt = validation.validate_output(db, ctx(db, ids), ref)
        assert receipt["status"] == "passed", receipt
        assert receipt["technical_ready"] and receipt["coverage"]["full_decode"]
        assert receipt["coverage"]["hash_bytes"] == len(data)
        assert receipt["cache_hit"] is False and "creative_quality" in receipt["unassessed"]
        assert receipt["validator_version"] == validation.VALIDATOR_VERSION
        assert "storage_uri" not in json.dumps(receipt)
        generation = db.get(Generation, uuid.UUID(ref["id"]))
        stored = db.get(GenerationJob, generation.job_id).cost_raw[validation.CACHE_KEY][
            ref["expected_blob_hash"]
        ]
        assert stored == receipt
        db.commit()
    validation._CACHE.clear()

    def unexpected_decode(*args, **kwargs):
        raise AssertionError("verified immutable bytes should not be decoded again")

    monkeypatch.setattr(validation, "_validate", unexpected_decode)
    with sessions() as db:
        cached = validation.validate_output(db, ctx(db, ids), ref)
        assert cached["status"] == "passed" and cached["cache_hit"]
        assert validation.cached_validation(db, ctx(db, ids), ref)["cache_hit"]


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
def test_corrupt_output_with_valid_cas_record_cannot_be_delivered(studio, kind):
    _, sessions, ids = studio
    ref = source(
        sessions,
        ids,
        b"this is not decodable media",
        kind,
        duration_ms=1000 if kind != "image" else None,
    )
    with sessions() as db:
        receipt = validation.validate_output(db, ctx(db, ids), ref)
        assert receipt["status"] == "failed" and not receipt["technical_ready"]
        assert receipt["error"]["code"] == "DECODE_FAILED"
        assert receipt["coverage"]["full_decode"] is False


def test_truncated_faststart_video_and_wrong_stream_are_not_success(studio, tmp_path):
    _, sessions, ids = studio
    data = video(tmp_path / "good.mp4")
    truncated = source(sessions, ids, data[: int(len(data) * 0.65)], "video", duration_ms=1000)
    wrong_stream = source(sessions, ids, sound(1), "video", duration_ms=1000)
    with sessions() as db:
        first = validation.validate_output(db, ctx(db, ids), truncated)
        assert first["status"] == "failed", first
        assert first["error"]["code"] in {"DECODE_FAILED", "INCOMPLETE_DECODE"}
        second = validation.validate_output(db, ctx(db, ids), wrong_stream)
        assert second["status"] == "failed" and second["error"]["code"] == "MISSING_STREAM"


def test_declared_duration_dimensions_and_required_audio_must_match(studio, tmp_path):
    _, sessions, ids = studio
    ref = source(
        sessions, ids, video(tmp_path / "silent.mp4", audio=False), "video", duration_ms=1000
    )
    with sessions() as db:
        for expected in ({"duration_ms": 2000}, {"width": 320}, {"has_audio": True}):
            receipt = validation.validate_output(db, ctx(db, ids), ref, expected=expected)
            assert receipt["status"] == "failed", receipt
            assert receipt["error"]["code"] == "EXPECTATION_MISMATCH"
        passed = validation.validate_output(
            db, ctx(db, ids), ref, expected={"duration_ms": 1000, "width": 160, "height": 90}
        )
        assert passed["status"] == "passed", passed
        assert passed["coverage"]["streams_checked"] == ["video"]


def test_changed_physical_bytes_and_cas_repair_invalidate_prior_pass(studio, tmp_path):
    _, sessions, ids = studio
    data = png("blue")
    ref = source(sessions, ids, data)
    with sessions() as db:
        receipt = validation.validate_output(db, ctx(db, ids), ref)
        assert receipt["status"] == "passed"
        blob = db.get(Blob, ref["expected_blob_hash"])
        path = Path(blob.storage_uri)
        path.write_bytes(data[: len(data) // 2])
        assert validation.cached_validation(db, ctx(db, ids), ref) is None
        failed = validation.validate_output(db, ctx(db, ids), ref, prior_receipt=receipt)
        assert failed["status"] == "failed" and failed["error"]["code"] == "CONTENT_MISMATCH"
        upload = tmp_path / "exact-original.png"
        upload.write_bytes(data)
        repaired = cas.put_file(db, upload, "image/png", force_store=True)
        assert repaired.storage_uri != str(path)
        healthy = validation.validate_output(db, ctx(db, ids), ref)
        assert healthy["status"] == "passed" and not healthy["cache_hit"]


def test_budget_timeout_cancellation_and_animation_are_not_checked_or_unsupported(
    studio, tmp_path, monkeypatch
):
    _, sessions, ids = studio
    ref = source(sessions, ids, video(tmp_path / "good.mp4"), "video", duration_ms=1000)
    animation = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(
        animation,
        "PNG",
        save_all=True,
        append_images=[Image.new("RGB", (16, 16), "blue")],
        duration=100,
        loop=0,
    )
    animated = source(sessions, ids, animation.getvalue())
    with sessions() as db:
        unsupported = validation.validate_output(db, ctx(db, ids), animated)
        assert unsupported["status"] == "unsupported"
        canceled = validation.validate_output(db, ctx(db, ids), ref, canceled=lambda: True)
        assert canceled["status"] == "not_checked" and canceled["error"]["code"] == "CANCELED"
        monkeypatch.setattr(validation, "MAX_DURATION_MS", 500)
        bounded = validation.validate_output(db, ctx(db, ids), ref)
        assert bounded["status"] == "not_checked" and bounded["error"]["code"] == "DURATION_LIMIT"
        monkeypatch.setattr(validation, "MAX_DURATION_MS", 1800000)

        def timeout(*args, **kwargs):
            raise ValueError("视频处理超时")

        monkeypatch.setattr(media_engine, "run", timeout)
        timed_out = validation.validate_output(db, ctx(db, ids), ref)
        assert timed_out["status"] == "not_checked" and timed_out["error"]["code"] == "TIME_LIMIT"


def test_state_receipt_reuse_requires_exact_validator_hash_and_expectations(studio, monkeypatch):
    _, sessions, ids = studio
    ref = source(sessions, ids, sound(1), "audio", library=True, duration_ms=1000)
    with sessions() as db:
        prepared = validation.prepare_validation(db, ctx(db, ids), ref)
    # The slow phase needs neither a DB session nor a held production-run row.
    receipt = validation.validate_prepared(prepared)
    assert receipt["status"] == "passed"
    validation._CACHE.clear()
    prior = json.loads(json.dumps(receipt))
    wrong = copy.deepcopy(prior)
    wrong["validator_version"] = "obsolete"
    assert validation.receipt_matches(prepared, prior)
    assert not validation.receipt_matches(prepared, wrong)
    wrong = {**prior, "expected_fingerprint": "wrong"}
    assert not validation.receipt_matches(prepared, wrong)
    monkeypatch.setattr(
        validation, "_validate", lambda *_: pytest.fail("re-decoded persisted state")
    )
    cached = validation.validate_prepared(prepared, prior_receipt=prior)
    assert cached["cache_hit"] and cached["status"] == "passed"


def test_access_rechecked_after_decode_and_partial_observations_not_accepted(studio):
    _, sessions, ids = studio
    ref = source(sessions, ids, sound(1), "audio", duration_ms=1000)
    with sessions() as db:
        with pytest.raises(ValueError):
            validation.prepare_validation(db, ctx(db, ids), {**ref, "start_ms": 0, "end_ms": 500})
        prepared = validation.prepare_validation(db, ctx(db, ids), ref)
        receipt = validation.validate_prepared(prepared)
        member = db.scalar(select(Membership).where(Membership.user_id == ids["user"]))
        db.delete(member)
        db.commit()
        with pytest.raises(Forbidden):
            validation.record_validation(db, ctx(db, ids), prepared, receipt)


def test_oversized_object_is_not_downloaded_and_validator_never_adopts(studio, monkeypatch):
    _, sessions, ids = studio
    ref = source(sessions, ids, png("red"))
    with sessions() as db:
        blob = db.get(Blob, ref["expected_blob_hash"])
        blob.size_bytes = validation.MAX_SOURCE_BYTES + 1
        db.flush()
        monkeypatch.setattr(cas, "open_range", lambda *a, **k: pytest.fail("oversized object read"))
        receipt = validation.validate_output(db, ctx(db, ids), ref)
        assert receipt["status"] == "not_checked"
        assert receipt["error"]["code"] == "SOURCE_SIZE_LIMIT"
        assert db.get(Generation, uuid.UUID(ref["id"])).is_selected is False
