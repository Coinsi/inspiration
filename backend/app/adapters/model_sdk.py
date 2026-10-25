"""In-process model transport. Routing, billing and retries belong to our job layer."""

import os
from contextlib import contextmanager
from functools import lru_cache

from app.core.errors import ProviderError


@contextmanager
def single_submission_client(endpoint, token):
    """Transport fence for the pinned SDK's OpenAI image retry override."""
    import httpx
    from openai import OpenAI

    attempted = False
    first_status = None

    def before_send(request):
        nonlocal attempted
        if request.method == "POST":
            if attempted:
                raise ProviderError("生成提交结果待核实，已阻止 SDK 自动重复提交")
            attempted = True

    def after_receive(response):
        nonlocal first_status
        if first_status is None:
            first_status = response.status_code

    with httpx.Client(
        event_hooks={"request": [before_send], "response": [after_receive]}
    ) as transport:
        with OpenAI(
            api_key=token, base_url=endpoint, max_retries=0, http_client=transport
        ) as client:
            try:
                yield client
            except Exception as exc:
                # Preserve the original rejection/timeout status if a blocked
                # automatic retry was wrapped by the SDK as a generic 500.
                if first_status is not None and first_status >= 400:
                    exc.status_code = first_status
                raise


@lru_cache(maxsize=1)
def sdk():
    # Do not fetch a remote price catalogue during API/worker startup.
    # Prices in this application come from the saved channel configuration.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    return litellm


def connection(endpoint, token, provider="openai"):
    if not token:
        raise ProviderError("请先配置模型服务密钥")
    return {
        "api_key": token,
        "api_base": endpoint or None,
        "custom_llm_provider": provider,
        "num_retries": 0,
        "max_retries": 0,
        "drop_params": False,
        "timeout": 90,
    }


def failure(exc, operation="模型请求"):
    # SDK exception text may contain request bodies, bearer keys or signed URLs.
    status = getattr(exc, "status_code", None)
    suffix = f"（HTTP {status}）" if isinstance(status, int) else ""
    return ProviderError(f"{operation}失败{suffix}，请核对模型能力、渠道地址和服务状态")


def chat(
    system,
    user,
    endpoint,
    token,
    model,
    *,
    provider="openai",
    max_output_tokens=None,
    temperature=0.4,
):
    from app.adapters.assist.streaming import read_sdk_completion

    params = connection(endpoint, token, provider)
    if max_output_tokens is not None:
        params["max_completion_tokens"] = max_output_tokens
    try:
        response = sdk().completion(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            response_format={"type": "json_object"},
            stream=True,
            stream_options={"include_usage": True},
            **params,
        )
        return read_sdk_completion(response)
    except ProviderError:
        raise
    except Exception as exc:
        raise failure(exc) from None
