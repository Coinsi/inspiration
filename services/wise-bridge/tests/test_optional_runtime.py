"""Exact-source guarded microbatch contracts; no real model/GPU inference."""

import hashlib
from pathlib import Path

import pytest
from wise_bridge import optional_runtime as runtime
from wise_bridge.optional_models import OptionalModelUnavailable


class Batch:
    def __init__(self, values):
        self.values = values
        self.shape = (len(values), 3, 960, 960)

    def __getitem__(self, index):
        return Batch(self.values[index])


def original(_self, images):
    _self.calls.append(images.shape[0])
    return [
        {"vector": value, "metadata": _self.metadata[value]} for value in images.values
    ]


def test_all_eight_frames_call_original_one_at_a_time_preserving_order_and_metadata(
    monkeypatch,
):
    source = (
        Path(__file__).parent / "fixtures" / "pinned_object_extraction.txt"
    ).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == runtime.EXTRACTION_SHA256
    monkeypatch.setattr(runtime.inspect, "getsource", lambda _: source)
    wrapped = runtime.microbatch_method(original)
    instance = type("Extractor", (), {})()
    instance.calls, instance.metadata = [], [object() for _ in range(8)]
    results = wrapped(instance, Batch(list(range(8))))
    assert instance.calls == [1] * 8
    assert [row["vector"] for row in results] == list(range(8))
    assert all(
        row["metadata"] is instance.metadata[index] for index, row in enumerate(results)
    )


def test_unknown_source_is_not_silently_microbatched():
    with pytest.raises(RuntimeError, match="source changed"):
        runtime.microbatch_method(original)


def test_auto_device_and_missing_cuda_do_not_fall_back_to_cpu(monkeypatch):
    with pytest.raises(OptionalModelUnavailable, match="model_device_unspecified"):
        runtime.check_device_configuration({"default": {"compile": False}})
    assert runtime.check_device_configuration({"default": {"device": "cpu"}}) == "cpu"
    monkeypatch.setattr(runtime.Path, "exists", lambda _: False)
    with pytest.raises(OptionalModelUnavailable, match="model_device_unavailable"):
        runtime.check_device_configuration({"default": {"device": "cuda"}})


def test_remote_encoder_configuration_is_not_accepted_as_verified_local_weights():
    with pytest.raises(OptionalModelUnavailable, match="model_device_unspecified"):
        runtime.check_device_configuration(
            {"default": {"device": "cpu", "url": "http://elsewhere"}}
        )


def test_wsl_dxg_device_passes_lightweight_preflight(monkeypatch):
    monkeypatch.setattr(runtime, "cuda_driver_device_count", lambda: 1)
    monkeypatch.setattr(runtime.Path, "exists", lambda path: path.name == "dxg")
    monkeypatch.setattr(runtime.Path, "glob", lambda *_: iter(()))
    assert runtime.gpu_device_nodes_available()
    assert runtime.check_device_configuration({"default": {"device": "cuda"}}) == "cuda"


def test_native_nvidia_requires_control_and_actual_gpu_node(monkeypatch):
    monkeypatch.setattr(runtime, "cuda_driver_device_count", lambda: 1)
    monkeypatch.setattr(runtime.Path, "exists", lambda path: path.name == "nvidiactl")
    monkeypatch.setattr(runtime.Path, "glob", lambda *_: iter(()))
    assert not runtime.gpu_device_nodes_available()
    with pytest.raises(OptionalModelUnavailable, match="model_device_unavailable"):
        runtime.check_device_configuration({"default": {"device": "cuda"}})
    monkeypatch.setattr(runtime.Path, "glob", lambda *_: iter([Path("/dev/nvidia0")]))
    assert runtime.gpu_device_nodes_available()


def test_wsl_node_without_usable_cuda_driver_stays_unavailable(monkeypatch):
    monkeypatch.setattr(runtime.Path, "exists", lambda path: path.name == "dxg")
    monkeypatch.setattr(runtime, "cuda_driver_device_count", lambda: 0)
    with pytest.raises(OptionalModelUnavailable, match="model_device_unavailable"):
        runtime.check_device_configuration({"default": {"device": "cuda"}})


def test_driver_errors_and_missing_library_fail_closed(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        runtime, "_cuda_driver", lambda: SimpleNamespace(cuInit=lambda _: 100)
    )
    assert runtime.cuda_driver_device_count() == 0

    def unavailable():
        raise OSError("No GPU driver exposed")

    monkeypatch.setattr(runtime, "_cuda_driver", unavailable)
    assert runtime.cuda_driver_device_count() == 0
