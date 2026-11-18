"""Documented output types for the MiniMax catalog, whose entries can contain only IDs."""

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
    "MiniMax-H3": "video",
    "MiniMax-Hailuo-2.3": "video",
    "MiniMax-Hailuo-2.3-Fast": "video",
    "MiniMax-Hailuo-02": "video",
    "MiniMax-H3-Image": "image",
    "MiniMax-H3-Image-Highspeed": "image",
    "image-01": "image",
    "image-01-live": "image",
}


def expected_output(model, *, template_id=None, protocol=None):
    if template_id == "minimax" or protocol in ("minimax_image", "minimax_video"):
        return MODEL_OUTPUTS.get(model)
    return None
