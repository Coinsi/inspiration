"""The OpenAI-compatible /models endpoint can return only text IDs even though
the platform documents separate image and video APIs.
"""

DOCUMENTATION_URL = "https://platform.minimax.cn/docs/guides/models-intro"
OFFICIAL_ENDPOINTS = {
    "https://api.minimax.cn/v1",
    "https://api.minimaxi.com/v1",
    "https://api.minimax.io/v1",
}
_VIDEO_BASES = {
    base.removesuffix("/v1") + suffix
    for base in OFFICIAL_ENDPOINTS
    for suffix in ("", "/v1", "/v2")
}

H3_PROTOCOL = "minimax_h3_video"
# Minimum duration and supported output resolutions from the V2 video contract.
H3_VIDEO_MODELS = {
    "MiniMax-H3": (4, ["768P", "2K"]),
    "MiniMax-H3-Highspeed": (4, ["480P", "768P"]),
    "MiniMax-H3-Lite": (4, ["480P", "768P"]),
    "MiniMax-H3-Max": (5, ["480P", "768P"]),
}
H3_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]


def video_protocol(endpoint, model, protocol):
    if model not in H3_VIDEO_MODELS:
        return protocol
    base = (endpoint or "").rstrip("/")
    if protocol == "minimax_video" or (protocol == "openai_video" and base in _VIDEO_BASES):
        return H3_PROTOCOL
    return protocol


def video_profile(config, endpoint):
    """Resolve live profiles before freezing jobs. Old receipts keep their original protocol."""
    if config.get("modality") != "video":
        return config
    model = config.get("model")
    protocol = video_protocol(endpoint, model, config.get("protocol"))
    if protocol != H3_PROTOCOL:
        return config
    minimum, resolutions = H3_VIDEO_MODELS.get(model, H3_VIDEO_MODELS["MiniMax-H3"])
    maximum = min(int(config.get("max_video_seconds", 15)), 15)
    supported = {
        "duration": list(range(minimum, maximum + 1)),
        "resolution": resolutions,
        "aspect_ratio": H3_RATIOS,
    }
    configured = config.get("param_schema") or {}
    schema = {}
    for key, supported_values in supported.items():
        values = configured.get(key, {}).get("enum", supported_values)
        schema[key] = {"enum": [value for value in values if value in supported_values]}
    return {**config, "protocol": protocol, "max_video_seconds": maximum, "param_schema": schema}


MODEL_OUTPUTS = {
    "MiniMax-M3": "text",
    "MiniMax-M3.1-Flash": "text",
    "MiniMax-M2": "text",
    "MiniMax-M2.1": "text",
    "MiniMax-M2.1-highspeed": "text",
    "MiniMax-M2.5": "text",
    "MiniMax-M2.5-highspeed": "text",
    "MiniMax-M2.7": "text",
    "MiniMax-M2.7-highspeed": "text",
    "MiniMax-Hailuo-2.3": "video",
    "MiniMax-Hailuo-2.3-Fast": "video",
    "MiniMax-Hailuo-02": "video",
    **dict.fromkeys(H3_VIDEO_MODELS, "video"),
    "image-01": "image",
    "image-01-live": "image",
    "MiniMax-H3-Image": "image",
    "MiniMax-H3-Image-Highspeed": "image",
}

_H3_IMAGE_NOTE = "H3 图片模型使用新版接口，当前内置 MiniMax 图片连接支持 image-01 系列。"
MEDIA_NOTES = {
    **dict.fromkeys(H3_VIDEO_MODELS, "使用 MiniMax 新版视频接口，支持文字和首尾帧图片。"),
    "MiniMax-H3-Image": _H3_IMAGE_NOTE,
    "MiniMax-H3-Image-Highspeed": _H3_IMAGE_NOTE,
    "MiniMax-Hailuo-2.3-Fast": "图生视频，需要一张首帧图片。",
    "image-01-live": "图生图，需要一张参考图。",
}


def documented_media(reported_ids, purpose):
    """Keep account discovery separate from models listed in the public docs."""
    candidates = []
    for code, output in MODEL_OUTPUTS.items():
        if output == "text" or purpose not in (None, output) or code in reported_ids:
            continue
        item = {"id": code, "outputs": [output]}
        if code in MEDIA_NOTES:
            item["note"] = MEDIA_NOTES[code]
        candidates.append(item)
    return candidates


def expected_output(model, *, template_id=None, protocol=None):
    if template_id == "minimax" or protocol in ("minimax_image", "minimax_video", H3_PROTOCOL):
        return MODEL_OUTPUTS.get(model)
    return None
