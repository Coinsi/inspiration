"""The OpenAI-compatible /models endpoint can return only text IDs even though
the platform documents separate image and video APIs.
"""

DOCUMENTATION_URL = "https://platform.minimax.cn/docs/guides/models-intro"
OFFICIAL_ENDPOINTS = {
    "https://api.minimax.cn/v1",
    "https://api.minimaxi.com/v1",
    "https://api.minimax.io/v1",
}

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
    "MiniMax-H3": "video",
    "image-01": "image",
    "image-01-live": "image",
    "MiniMax-H3-Image": "image",
    "MiniMax-H3-Image-Highspeed": "image",
}

_H3_IMAGE_NOTE = "H3 图片模型使用新版接口，当前内置 MiniMax 图片连接支持 image-01 系列。"
MEDIA_NOTES = {
    "MiniMax-H3": "H3 使用新版视频接口，当前内置 MiniMax 视频连接支持海螺 2.3 和 02 系列。",
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
    if template_id == "minimax" or protocol in ("minimax_image", "minimax_video"):
        return MODEL_OUTPUTS.get(model)
    return None
