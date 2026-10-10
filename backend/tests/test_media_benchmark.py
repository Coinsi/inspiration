"""Safety and resumability of the opt-in benchmark, with isolated real PG coverage."""

import json
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from scripts import benchmark_media_pipeline as bench


@pytest.mark.parametrize(
    "flags",
    [
        ["--count", "5"],
        ["--duration", "3600"],
        ["--duration", "nan"],
        ["--reserve-gib", "0"],
        ["--concurrency", "2"],
        ["--providers", "wise"],
        ["--providers", "unknown", "--allow-external"],
    ],
)
def test_large_or_remote_workload_requires_explicit_admission(flags):
    with pytest.raises(bench.BenchmarkError):
        bench.validate_plan(bench.parse_args(flags))


def test_gpu_metrics_records_whole_device_memory_and_nullable_unsupported_fields(monkeypatch):
    calls = []

    def read(args, timeout):
        calls.append((args, timeout))
        return SimpleNamespace(
            returncode=0, stdout="0, 1024, 8188, 75\n1, N/A, 8192, [Not Supported]\n"
        )

    monkeypatch.setattr(bench, "command", read)
    result = bench.gpu_metrics("inspiration-wise-services-wise-1")
    assert result["available"] is True
    assert result["devices"][0]["memory_used_bytes"] == 1024**3
    assert result["devices"][0]["utilization_percent"] == 75
    assert result["devices"][1]["memory_used_bytes"] is None
    assert result["devices"][1]["utilization_percent"] is None
    assert "other processes" in result["scope"]
    assert calls[0][1] == 3
    assert "--format=csv,noheader,nounits" in calls[0][0]


@pytest.mark.parametrize(
    "code,output", [(1, ""), (0, ""), (0, "unexpected"), (0, "0, nan, 8188, 20")]
)
def test_missing_or_invalid_gpu_is_unavailable_not_zero(monkeypatch, code, output):
    monkeypatch.setattr(
        bench, "command", lambda *a, **k: SimpleNamespace(returncode=code, stdout=output)
    )
    result = bench.gpu_metrics("inspiration-wise-services-wise-1")
    assert result["available"] is False
    assert "devices" not in result and "memory_used_bytes" not in result


def test_gpu_sampling_is_bounded_and_only_targets_known_wise_container(monkeypatch):
    calls = []

    def timeout(args, timeout):
        calls.append(args)
        raise bench.subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(bench, "command", timeout)
    assert bench.gpu_metrics("inspiration-wise-services-wise-1")["available"] is False
    assert len(calls) == 1
    assert bench.gpu_metrics("another-container")["available"] is False
    assert len(calls) == 1


def test_disk_admission_holds_before_any_upload(monkeypatch, tmp_path):
    plan = bench.validate_plan(bench.parse_args([]))
    budget = bench.estimate_budget(plan)
    require = budget["combined_peak_estimate_bytes"] + budget["reserve_bytes"]
    monkeypatch.setattr(bench.shutil, "disk_usage", lambda _: SimpleNamespace(free=require - 1))
    with pytest.raises(bench.BenchmarkError, match="Insufficient"):
        bench.check_disk(tmp_path, budget)
    monkeypatch.setattr(bench.shutil, "disk_usage", lambda _: SimpleNamespace(free=require))
    assert bench.check_disk(tmp_path, budget)["required_bytes"] == require


def test_batch_requires_successful_matching_previous_tier(tmp_path):
    first = bench.open_evidence(bench.parse_args(["--root", str(tmp_path)]))
    next_flags = [
        "--root",
        str(tmp_path),
        "--count",
        "5",
        "--allow-heavy",
        "--previous-run",
        first.state["run_id"],
    ]
    with pytest.raises(bench.BenchmarkError, match="not been verified"):
        bench.open_evidence(bench.parse_args(next_flags))
    first.state.update(verified=True, cleaned=True, schema_dropped=True)
    first.save()
    second = bench.open_evidence(bench.parse_args(next_flags))
    assert second.state["plan"]["count"] == 5
    with pytest.raises(bench.BenchmarkError, match="lighter"):
        bench.open_evidence(bench.parse_args([*next_flags, "--duration", "3600"]))


@pytest.mark.parametrize("count,previous_count", [(5, 1), (10, 5), (50, 10)])
@pytest.mark.parametrize(
    "missing_field,value",
    [("cleaned", False), ("schema_dropped", False), ("cleaned", None), ("schema_dropped", None)],
)
def test_batch_rejects_verified_but_not_fully_reclaimed_predecessor(
    tmp_path, count, previous_count, missing_field, value
):
    first = bench.open_evidence(bench.parse_args(["--root", str(tmp_path)]))
    # Unit evidence only: no media, database or model task is created.
    first.state["plan"]["count"] = previous_count
    first.state.update(verified=True, cleaned=True, schema_dropped=True)
    if value is None:
        first.state.pop(missing_field)
    else:
        first.state[missing_field] = value
    first.save()
    flags = [
        "--root",
        str(tmp_path),
        "--count",
        str(count),
        "--allow-heavy",
        "--previous-run",
        first.state["run_id"],
    ]
    with pytest.raises(bench.BenchmarkError, match="fully cleaned"):
        bench.open_evidence(bench.parse_args(flags))
    assert list(tmp_path.glob("*/evidence.json")) == [first.path]
    first.state.update(cleaned=True, schema_dropped=True)
    first.save()
    assert bench.open_evidence(bench.parse_args(flags)).state["plan"]["count"] == count


def duration_predecessor(root, seconds=6):
    """Small evidence fixture, not a claim that these unit tests ran a model."""
    evidence = bench.open_evidence(bench.parse_args(["--root", str(root)]))
    evidence.state.update(
        verified=True,
        cleaned=True,
        schema_dropped=True,
        completed=[*bench.PHASES, "cleanup"],
        samples=[{"duration_ms": seconds * 1000}],
    )
    evidence.state["plan"].update(providers=["mediacms", "wise"], duration=seconds)
    evidence.save()
    return evidence


def external_duration_flags(root, seconds, predecessor=None):
    flags = [
        "--root",
        str(root),
        "--providers",
        "mediacms,wise",
        "--allow-external",
        "--allow-heavy",
        "--duration",
        str(seconds),
    ]
    if predecessor:
        flags += ["--previous-duration-run", predecessor.state["run_id"]]
    return flags


@pytest.mark.parametrize("seconds,prior_seconds", [(60, 6), (300, 60), (900, 300), (3600, 900)])
def test_external_duration_ladder_records_actual_predecessor(tmp_path, seconds, prior_seconds):
    prior = duration_predecessor(tmp_path, prior_seconds)
    evidence = bench.open_evidence(
        bench.parse_args(external_duration_flags(tmp_path, seconds, prior))
    )
    assert evidence.state["duration_gate"]["tier_seconds"] == seconds
    assert evidence.state["duration_gate"]["previous_observed_seconds"] == prior_seconds


@pytest.mark.parametrize("seconds", [60, 300, 900, 3600])
def test_external_long_duration_needs_separate_admission(tmp_path, seconds):
    with pytest.raises(bench.BenchmarkError, match="previous-duration-run"):
        bench.open_evidence(bench.parse_args(external_duration_flags(tmp_path, seconds)))
    assert not list(tmp_path.glob("*/evidence.json"))


@pytest.mark.parametrize("broken", ["unverified", "uncleaned", "batch", "provider", "phases"])
def test_duration_gate_rejects_incomplete_or_unrelated_evidence(tmp_path, broken):
    prior = duration_predecessor(tmp_path)
    if broken == "unverified":
        prior.state["verified"] = False
    elif broken == "uncleaned":
        prior.state["cleaned"] = False
    elif broken == "batch":
        prior.state["plan"]["count"] = 50
        prior.state["samples"] *= 50
    elif broken == "provider":
        prior.state["plan"]["providers"] = ["wise"]
    else:
        prior.state["completed"].remove("wise")
    prior.save()
    with pytest.raises(bench.BenchmarkError):
        bench.open_evidence(bench.parse_args(external_duration_flags(tmp_path, 60, prior)))


def test_duration_gate_cannot_use_nominal_plan_or_batch_flag_to_skip_a_step(tmp_path):
    prior = duration_predecessor(tmp_path, 6)
    prior.state["plan"]["duration"] = 60
    prior.save()
    with pytest.raises(bench.BenchmarkError, match="actually verified 60s"):
        bench.open_evidence(bench.parse_args(external_duration_flags(tmp_path, 300, prior)))
    with pytest.raises(bench.BenchmarkError, match="previous-duration-run"):
        bench.open_evidence(
            bench.parse_args(
                [*external_duration_flags(tmp_path, 60), "--previous-run", prior.state["run_id"]]
            )
        )


def test_source_actual_duration_cannot_hide_behind_default_six_seconds(tmp_path, monkeypatch):
    from app.modules.library import media_ingest

    path = tmp_path / "long.mp4"
    path.write_bytes(b"metadata probe is isolated in this unit test")
    monkeypatch.setattr(media_ingest, "probe_media", lambda *_: {"duration_ms": 3_600_000})
    args = bench.parse_args([*external_duration_flags(tmp_path, 6), "--source", str(path)])
    with pytest.raises(bench.BenchmarkError, match="3600s needs.*900s"):
        bench.open_evidence(args)
    assert not list(tmp_path.glob("*/evidence.json"))


def test_provider_rechecks_measured_duration_before_contacting_remote(tmp_path):
    args = bench.parse_args(external_duration_flags(tmp_path, 6))
    evidence = bench.open_evidence(args)
    evidence.state["samples"] = [{"duration_ms": 3_600_000}]
    # No database/session/client was connected: rejection must happen before upload.
    with pytest.raises(bench.BenchmarkError, match="3600s needs.*900s"):
        bench.Pipeline(evidence, args).provider("wise", {})


def test_duration_resume_revalidates_anchor_but_always_allows_scoped_cleanup(tmp_path):
    prior = duration_predecessor(tmp_path)
    evidence = bench.open_evidence(bench.parse_args(external_duration_flags(tmp_path, 60, prior)))
    flags = ["--root", str(tmp_path), "--resume", evidence.state["run_id"], "--allow-external"]
    assert bench.open_evidence(bench.parse_args(flags)).state["duration_gate"]
    alternative = duration_predecessor(tmp_path)
    with pytest.raises(bench.BenchmarkError, match="cannot change"):
        bench.open_evidence(
            bench.parse_args([*flags, "--previous-duration-run", alternative.state["run_id"]])
        )
    prior.path.unlink()
    with pytest.raises(bench.BenchmarkError, match="does not exist"):
        bench.open_evidence(bench.parse_args(flags))
    assert bench.open_evidence(bench.parse_args([*flags, "--phase", "cleanup"]))


def test_provider_free_hour_run_retains_existing_basic_pipeline_admission(tmp_path):
    evidence = bench.open_evidence(
        bench.parse_args(["--root", str(tmp_path), "--duration", "3600", "--allow-heavy"])
    )
    assert evidence.state["duration_gate"] is None


def test_evidence_never_persists_exception_credentials(tmp_path):
    evidence = bench.open_evidence(bench.parse_args(["--root", str(tmp_path)]))
    secret = "postgresql://user:PRIVATE_SECRET@host/db"
    with pytest.raises(RuntimeError):
        with evidence.phase("verify"):
            raise RuntimeError(secret)
    raw = evidence.path.read_text(encoding="utf-8")
    assert secret not in raw and "PRIVATE_SECRET" not in raw
    assert json.loads(raw)["attempts"][-1]["status"] == "failed"
    assert "verify" not in evidence.state["completed"]


def test_run_lock_and_path_cannot_escape_owned_directory(tmp_path):
    with pytest.raises(bench.BenchmarkError):
        bench.run_folder(tmp_path, "../production")
    with bench.exclusive_run(tmp_path):
        with pytest.raises(bench.BenchmarkError, match="Another benchmark"):
            with bench.exclusive_run(tmp_path):
                pytest.fail("Concurrent workload admitted")
    with bench.exclusive_run(tmp_path):
        pass


def test_native_rss_sampler_includes_current_process():
    result = bench.process_rss()
    assert result["self_rss_bytes"] > 0
    assert result["process_tree_rss_bytes"] >= result["self_rss_bytes"]
    assert bench.descendants(
        {1: {"parent": 0}, 2: {"parent": 1}, 3: {"parent": 2}, 4: {"parent": 0}}, 1
    ) == {1, 2, 3}


@pytest.fixture
def short_source(tmp_path):
    from app.modules.generation import media_engine

    path = tmp_path / "external-original.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=12:d=1",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


def test_duplicates_cannot_count_as_independent_samples(tmp_path, short_source):
    args = bench.parse_args(["--root", str(tmp_path / "runs"), "--source", str(short_source)])
    evidence = bench.open_evidence(args)
    evidence.state["plan"]["count"] = 2
    evidence.state["plan"]["source_paths"] *= 2
    with pytest.raises(bench.BenchmarkError, match="Duplicate source hash"):
        bench.Pipeline(evidence, args).sources({})


def test_real_upload_resume_recovery_verify_and_scoped_cleanup(tmp_path, short_source, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for disposable PostgreSQL integration")
    monkeypatch.setenv("BENCHMARK_DATABASE_URL", url)
    root = tmp_path / "runs"
    original_digest = bench.sha256(short_source)
    args = bench.parse_args(
        [
            "--root",
            str(root),
            "--source",
            str(short_source),
            "--exercise-recovery",
        ]
    )
    evidence = bench.open_evidence(args)
    pipeline = bench.Pipeline(evidence, args)
    created_version = None
    with pipeline.connect():
        pipeline.sources({})
        # Exercise true ASGI upload offset reconciliation, as if the CLI had stopped.
        sample = evidence.state["samples"][0]
        version = pipeline.request(
            "POST",
            "/uploads",
            json={
                "filename": short_source.name,
                "size_bytes": short_source.stat().st_size,
                "fingerprint": original_digest,
            },
        )
        pipeline.request(
            "POST",
            f"/versions/{version['id']}/chunk?offset=0",
            files={"file": ("chunk", short_source.read_bytes())},
        )
        pipeline.upload({})
        assert sample["version_id"] == version["id"]
        assert sample["upload_resume_offsets"] == [short_source.stat().st_size]
        created_version = version["id"]
        pipeline.preview({})
        assert sample["recovery"]["missing_staging_reported"]
        assert sample["recovery"]["cooperative_interruption_preserved_queue"]
        pipeline.search({})
        entry = {}
        pipeline.verify(entry)
        assert entry["samples"][0]["full_proxy_decode"]
        evidence.state["completed"] = ["sources", "upload", "preview", "search", "verify"]
        evidence.save()
    # A new driver instance uses the same database project and immutable version.
    resumed_args = bench.parse_args(["--root", str(root), "--resume", evidence.state["run_id"]])
    resumed = bench.open_evidence(resumed_args)
    new_pipeline = bench.Pipeline(resumed, resumed_args)
    with new_pipeline.connect():
        assert new_pipeline.request("GET", f"/versions/{created_version}")["status"] == "ready"
        new_pipeline.cleanup({})
        resumed.save()
    assert resumed.state["cleaned"]
    assert short_source.exists() and bench.sha256(short_source) == original_digest
    assert evidence.path.exists() and not (evidence.folder / "cas").exists()
    admin = create_engine(url)
    try:
        with admin.connect() as conn:
            assert not conn.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname=:n"), {"n": new_pipeline.schema}
            )
    finally:
        admin.dispose()


def test_invalid_provider_phase_is_not_reported_as_passed(tmp_path, capsys):
    assert bench.main(["--root", str(tmp_path), "--phase", "wise"]) == 1
    assert "not included" in capsys.readouterr().out
    state = json.loads(next(tmp_path.glob("*/evidence.json")).read_text(encoding="utf-8"))
    assert "wise" not in state["completed"]


def test_resume_rejects_parallel_configuration(tmp_path):
    evidence = bench.open_evidence(bench.parse_args(["--root", str(tmp_path)]))
    with pytest.raises(bench.BenchmarkError, match="concurrency"):
        bench.open_evidence(
            bench.parse_args(
                [
                    "--root",
                    str(tmp_path),
                    "--resume",
                    evidence.state["run_id"],
                    "--concurrency",
                    "2",
                ]
            )
        )


def test_cleanup_cannot_touch_unowned_schema(tmp_path):
    evidence = bench.open_evidence(bench.parse_args(["--root", str(tmp_path)]))
    pipeline = bench.Pipeline(evidence, bench.parse_args([]))
    assert pipeline.schema == "bench_media_" + evidence.state["run_id"]

    class ForeignConnection:
        def scalar(self, *_args):
            return uuid.uuid4().hex

    with pytest.raises(bench.BenchmarkError, match="ownership"):
        pipeline.check_owner(ForeignConnection())
