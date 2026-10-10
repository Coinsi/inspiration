"""Real child-process protocol/lifetime checks, without model inference."""

import sys
import time

import pytest

from wise_bridge.config import Settings
from wise_bridge.query_errors import ReferenceQueryError
from wise_bridge.query_process import QueryWorkerError, ResidentQueryProcess


@pytest.fixture
def query_process(tmp_path):
    script = tmp_path / "protocol_fixture.py"
    script.write_text(
        """import json,os,sys,time,subprocess
for line in sys.stdin:
    request=json.loads(line)
    payload=request['payload']
    mode=payload.get('mode')
    if mode=='slow': time.sleep(30)
    if mode=='descendant':
        subprocess.Popen([sys.executable,'-c',"import time,pathlib;time.sleep(2);pathlib.Path("+repr(payload['marker'])+").write_text('leaked')"])
        time.sleep(30)
    if mode=='crash': os._exit(7)
    response={'request_id':request['request_id'],'result':{'pid':os.getpid(),'value':payload.get('value')}}
    if mode=='wrong_id': response['request_id']='wrong'
    if mode=='error': response={'request_id':request['request_id'],'error':'private detail must stay private'}
    if mode=='input_error': response={'request_id':request['request_id'],'input_error':payload.get('code')}
    print(json.dumps(response),flush=True)
""",
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path, token="test-query-process-token-12345", min_free_bytes=0
    )
    worker = ResidentQueryProcess(settings, [sys.executable, str(script)])
    try:
        yield worker
    finally:
        worker.close()


def test_repeated_queries_reuse_child_and_explicit_close_restarts(query_process):
    first = query_process.request({"modality": "visual", "value": 1})
    process = query_process.process
    second = query_process.request({"modality": "visual", "value": 2})
    # Windows venv python.exe can be a launcher with a different process ID.
    assert first["pid"] == second["pid"]
    assert query_process.process is process
    assert second["value"] == 2
    query_process.close()
    assert process.poll() is not None
    assert query_process.request({"modality": "visual"})["pid"] != first["pid"]


def test_timeout_terminates_child_and_next_request_recovers(query_process):
    query_process.request({"modality": "visual"})
    process = query_process.process
    with pytest.raises(QueryWorkerError, match="time limit"):
        query_process.request({"modality": "visual", "mode": "slow"}, timeout=0.2)
    assert process.poll() is not None
    assert query_process.process is None
    assert query_process.request({"modality": "visual"})["pid"] != process.pid


def test_running_query_cancellation_terminates_child(query_process):
    query_process.request({"modality": "visual"})
    process = query_process.process
    started = time.monotonic()
    with pytest.raises(QueryWorkerError, match="canceled"):
        query_process.request(
            {"modality": "visual", "mode": "slow"},
            canceled=lambda: time.monotonic() - started > 0.2,
        )
    assert process.poll() is not None
    assert query_process.process is None


@pytest.mark.parametrize("mode", ["wrong_id", "error", "crash"])
def test_bad_or_failed_responses_discard_process_and_allow_recovery(
    query_process, mode
):
    first = query_process.request({"modality": "visual"})
    with pytest.raises(QueryWorkerError) as error:
        query_process.request({"modality": "visual", "mode": mode})
    assert "private detail" not in str(error.value)
    assert query_process.process is None
    assert query_process.request({"modality": "visual"})["pid"] != first["pid"]


def test_model_budget_restarts_to_release_upstream_global_cache(query_process):
    first = query_process.request({"modality": "visual"})
    process = query_process.process
    second = query_process.request({"modality": "audio"})
    assert second["pid"] != first["pid"]
    assert process.poll() is not None
    assert query_process.models == {"audio"}


def test_same_model_new_feature_identity_restarts_cached_encoder(query_process):
    first_request = {"modality": "object", "feature_fingerprint": "a" * 64}
    first = query_process.request(first_request)
    process = query_process.process
    assert query_process.request(first_request)["pid"] == first["pid"]
    updated = query_process.request(
        {"modality": "object", "feature_fingerprint": "b" * 64}
    )
    assert process.poll() is not None
    assert updated["pid"] != first["pid"]
    assert query_process.models == {"object"}
    assert query_process.feature_fingerprints == {"object": "b" * 64}
    # Returning to a previously accepted model space still needs a fresh child;
    # upstream's process-global cache must never resurrect its old encoder.
    latest = query_process.process
    assert query_process.request(first_request)["pid"] != updated["pid"]
    assert latest.poll() is not None


@pytest.mark.parametrize("fingerprint", [None, "", "unknown", "g" * 64])
def test_optional_unidentified_query_cannot_reuse_a_live_child(
    query_process, fingerprint
):
    query_process.request({"modality": "visual"})
    process = query_process.process
    with pytest.raises(QueryWorkerError, match="fingerprint is missing"):
        query_process.request(
            {"modality": "object", "feature_fingerprint": fingerprint}
        )
    assert process.poll() is not None
    assert query_process.process is None
    assert query_process.models == set()
    assert query_process.feature_fingerprints == {}


def test_timeout_terminates_descendants_not_just_launcher(query_process, tmp_path):
    query_process.request({"modality": "visual"})
    marker = tmp_path / "descendant-survived"
    with pytest.raises(QueryWorkerError, match="time limit"):
        query_process.request(
            {"modality": "visual", "mode": "descendant", "marker": str(marker)},
            timeout=0.5,
        )
    time.sleep(2)
    assert not marker.exists()


@pytest.mark.parametrize("code", ["reference_no_object", "reference_region_no_match"])
def test_expected_reference_input_error_keeps_process_and_accounts_for_loaded_model(
    query_process, code
):
    payload = {
        "modality": "object",
        "feature_fingerprint": "a" * 64,
        "query_file": "/controlled/input",
    }
    # This is the first request: even an expected input failure has loaded the
    # model. Otherwise the next modality could exceed the resident memory cap.
    with pytest.raises(ReferenceQueryError) as error:
        query_process.request({**payload, "mode": "input_error", "code": code})
    assert error.value.code == code
    process = query_process.process
    assert process.poll() is None and query_process.models == {"object"}
    first = query_process.request(payload)
    assert query_process.process is process
    assert query_process.request(payload)["pid"] == first["pid"]
    assert query_process.request({"modality": "visual"})["pid"] != first["pid"]
    assert process.poll() is not None


@pytest.mark.parametrize(
    "code,capability,reference",
    [
        ("unknown /private/key", "object", True),
        (["reference_no_object"], "object", True),
        ("reference_no_object", "visual", True),
        ("reference_no_object", "object", False),
    ],
)
def test_unknown_or_out_of_scope_input_errors_are_protocol_failures(
    query_process, code, capability, reference
):
    with pytest.raises(QueryWorkerError) as error:
        query_process.request(
            {
                "modality": capability,
                "feature_fingerprint": "a" * 64,
                "mode": "input_error",
                "code": code,
                **({"query_file": "/controlled/input"} if reference else {}),
            }
        )
    assert "/private/key" not in str(error.value)
    assert query_process.process is None
