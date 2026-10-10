"""Optional model readiness comes from the private service, never URL/key presence."""

import hashlib
import re
import time

from app.core.config import settings
from app.core.errors import CapabilityUnsupported
from app.modules.library.providers.wise import WiseClient

OPTIONAL = ("face", "object")
MESSAGES = {
    "capability_disabled": "尚未启用此可选能力",
    "service_unavailable": "无法核对可选模型状态，请稍后重试",
    "service_not_configured": "素材处理服务尚未连接",
    "model_manifest_missing": "尚未准备本地模型清单",
    "model_manifest_invalid": "模型清单格式或固定版本不符",
    "model_file_missing": "模型文件尚未准备完整",
    "model_hash_mismatch": "模型文件校验不一致，需要重新核对",
    "model_layout_mismatch": "本地模型文件布局不符合固定版本要求",
    "model_revision_mismatch": "模型缓存未指向清单锁定版本",
    "dependency_manifest_invalid": "模型依赖清单不完整",
    "dependency_missing": "模型运行依赖尚未安装",
    "dependency_mismatch": "模型运行依赖版本不符",
    "offline_mode_required": "模型服务尚未配置离线运行",
    "license_use_unspecified": "尚未记录模型使用用途与许可核对",
    "license_record_missing": "尚未记录对应用途的模型许可依据",
    "license_scope_mismatch": "模型许可记录与使用用途不符",
    "model_validation_required": "尚未完成真实离线模型验收",
    "model_device_unspecified": "请为物体检索指定实际验收的运行设备",
    "model_device_unavailable": "物体模型指定的运行设备当前不可用",
    "model_fingerprint_mismatch": "物体模型配置已变化，需要重新验收或重建索引",
}
_cache = {}


def optional_states(*, refresh=False):
    result = {
        cap: {
            "configured": cap in settings.library_wise_capabilities,
            "ready": False,
            "reason": "capability_disabled",
        }
        for cap in OPTIONAL
    }
    requested = [cap for cap in OPTIONAL if result[cap]["configured"]]
    if not requested:
        return result
    if not (
        settings.library_processing_enabled
        and settings.library_wise_url
        and settings.library_wise_token
    ):
        for cap in requested:
            result[cap]["reason"] = "service_not_configured"
        return result
    key = (
        settings.library_wise_url,
        hashlib.sha256(settings.library_wise_token.encode()).hexdigest(),
        tuple(requested),
    )
    cached = _cache.get(key)
    if not refresh and cached and time.monotonic() - cached[0] < 5:
        return cached[1]
    try:
        health = WiseClient(settings.library_wise_url, settings.library_wise_token).health()
        if health.get("service") != "inspiration-wise-bridge":
            raise ValueError("Unexpected service")
        for cap in requested:
            remote = health.get("capabilities", {}).get(cap, {})
            reason = remote.get("reason")
            validation = remote.get("validation")
            # Trust only our authenticated private bridge's supported receipt
            # descriptor. A naked ready flag from older/configured services is
            # insufficient; face has no accepted inference protocol yet.
            accepted = (
                cap == "object"
                and remote.get("configured") is True
                and remote.get("ready") is True
                and remote.get("integrity_verified") is True
                and remote.get("availability") == "ready"
                and isinstance(validation, dict)
                and validation.get("protocol") == "wise-optional-probe-v1"
                and all(
                    isinstance(validation.get(key), str)
                    and re.fullmatch(r"[a-f0-9]{64}", validation[key])
                    for key in ("receipt_digest", "implementation_digest")
                )
            )
            result[cap]["ready"] = bool(accepted)
            result[cap]["reason"] = reason if reason in MESSAGES else "model_validation_required"
            result[cap]["integrity_verified"] = remote.get("integrity_verified") is True
            if accepted:
                result[cap]["reason"] = None
    except Exception:
        # Provider logs remain private; arbitrary upstream data is not browser text.
        for cap in requested:
            result[cap]["reason"] = "service_unavailable"
    _cache.clear()  # bounded; model refresh does not accumulate old credential keys
    _cache[key] = (time.monotonic(), result)
    return result


def require_optional(capabilities):
    requested = set(capabilities).intersection(OPTIONAL)
    if not requested:
        return
    states = optional_states(refresh=True)
    for cap in sorted(requested):
        if not states[cap]["ready"]:
            reason = states[cap]["reason"]
            raise CapabilityUnsupported(MESSAGES[reason], {"capability": cap, "reason": reason})
