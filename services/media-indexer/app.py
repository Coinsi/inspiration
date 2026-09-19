"""Self-hosted Qwen multimodal embedding endpoint; accepts bounded pixels, never URLs.

Protocol: normalized 512-dimensional MRL embeddings of text, images or sampled video.
Model architecture/pooling reference: https://github.com/QwenLM/Qwen3-VL-Embedding
Weights remain a separately downloaded Apache-2.0 model, not repository content.
"""

import base64
import io
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

MODEL = os.environ.get("INDEX_MODEL", "Qwen/Qwen3-VL-Embedding-2B")
REVISION = os.environ.get(
    "INDEX_MODEL_REVISION", "9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda"
)
MODEL_ID = os.environ.get("INDEX_MODEL_ID", "Qwen/Qwen3-VL-Embedding-2B")
KEY = f"{MODEL_ID}@{REVISION}:mrl512:video-v1"
TOKEN = os.environ.get("INDEX_TOKEN", "")
lock = threading.Lock()
model = processor = None


def authorize(authorization):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(_):
    yield


app = FastAPI(lifespan=lifespan)


class EmbedIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    images: list[str] = Field(default_factory=list, max_length=16)
    video: bool = False
    fps: float = Field(default=0.5, ge=0.05, le=30)


@app.get("/health")
def health(authorization: str | None = Header(None)):
    authorize(authorization)
    return {"model_key": KEY, "dimensions": 512, "loaded": model is not None}


@app.post("/embed")
def embed(data: EmbedIn, authorization: str | None = Header(None)):
    authorize(authorization)
    if not data.text.strip() and not data.images:
        raise HTTPException(422, "Text or image is required")
    if sum(map(len, data.images)) > 12_000_000:
        raise HTTPException(413, "Images too large")
    pictures = []
    try:
        for raw in data.images:
            image = Image.open(io.BytesIO(base64.b64decode(raw, validate=True)))
            if image.width * image.height > 4_000_000:
                raise ValueError("Image too large")
            pictures.append(image.convert("RGB"))
    except Exception as exc:
        raise HTTPException(422, "Invalid image") from exc
    with lock:
        return infer(data, pictures)


def infer(data, pictures):
    global model, processor
    import torch
    from model import EmbeddingModel
    from qwen_vl_utils import process_vision_info
    from transformers import AutoProcessor

    if model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        processor = AutoProcessor.from_pretrained(
            MODEL, revision=REVISION, padding_side="right"
        )
        loaded, info = EmbeddingModel.from_pretrained(
            MODEL,
            revision=REVISION,
            dtype=torch.float16 if device == "cuda" else torch.float32,
            attn_implementation="sdpa",
            trust_remote_code=False,
            output_loading_info=True,
        )
        if (
            info["missing_keys"]
            or info["unexpected_keys"]
            or info.get("mismatched_keys")
        ):
            del loaded
            raise HTTPException(
                503, "Embedding checkpoint does not match the model architecture"
            )
        model = loaded.to(device).eval()
    content = []
    if data.video and pictures:
        if len(pictures) == 1:
            pictures = pictures * 2
        content.append(
            {
                "type": "video",
                "video": pictures,
                "fps": data.fps,
                "max_pixels": 256 * 384,
                "min_pixels": 128 * 128,
                "total_pixels": 16 * 256 * 384,
            }
        )
    else:
        content.extend(
            {"type": "image", "image": p, "max_pixels": 256 * 384} for p in pictures
        )
    if data.text.strip():
        content.append({"type": "text", "text": data.text.strip()})
    messages = [
        {"role": "system", "content": "Represent the user's input."},
        {"role": "user", "content": content},
    ]
    prompt = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    images, videos, kwargs = process_vision_info(
        messages,
        image_patch_size=16,
        return_video_metadata=True,
        return_video_kwargs=True,
    )
    metadata = None
    if videos:
        videos, metadata = map(list, zip(*videos))
    inputs = processor(
        text=[prompt],
        images=images,
        videos=videos,
        video_metadata=metadata,
        do_resize=False,
        padding=True,
        return_tensors="pt",
        **kwargs,
    ).to(model.device)
    if inputs.input_ids.shape[1] > 8192:
        raise HTTPException(422, "Input exceeds embedding token budget")
    with torch.inference_mode():
        result = model(**inputs, use_cache=False)
        # Right padding: the final non-padding token is the document representation.
        last = int(inputs.attention_mask[0].sum()) - 1
        vector = torch.nn.functional.normalize(
            result.last_hidden_state[0, last, :512].float(), dim=0
        )
    return {"model_key": KEY, "embedding": vector.cpu().tolist()}
