"""Bounded self-hosted ASR. Receives PCM WAV chunks, never arbitrary URLs or paths."""

import base64
import io
import os
import threading
import wave

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MODEL = os.environ.get("ASR_MODEL", "Systran/faster-whisper-small")
REVISION = os.environ.get(
    "ASR_MODEL_REVISION", "536b0662742c02347bc0e980a01041f333bce120"
)
KEY = f"whisper-small@{REVISION}:int8:v1"
TOKEN = os.environ.get("ASR_TOKEN", "")
model = None
lock = threading.Lock()
app = FastAPI()


def authorize(value):
    if TOKEN and value != f"Bearer {TOKEN}":
        raise HTTPException(401, "Unauthorized")


class TranscribeIn(BaseModel):
    audio: str = Field(max_length=2_000_000)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


@app.get("/health")
def health(authorization: str | None = Header(None)):
    authorize(authorization)
    return {"model_key": KEY, "loaded": model is not None, "chunk_seconds": 30}


@app.post("/transcribe")
def transcribe(data: TranscribeIn, authorization: str | None = Header(None)):
    global model
    authorize(authorization)
    try:
        raw = base64.b64decode(data.audio, validate=True)
        with wave.open(io.BytesIO(raw)) as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != 16000
                or wav.getnframes() > 16000 * 31
            ):
                raise ValueError()
            duration = wav.getnframes() / 16000
    except Exception as exc:
        raise HTTPException(
            422, "Expected mono 16kHz PCM WAV, at most 31 seconds"
        ) from exc
    with lock:
        from faster_whisper import WhisperModel

        if model is None:
            model = WhisperModel(
                MODEL,
                device="cpu",
                compute_type="int8",
                cpu_threads=8,
                num_workers=1,
                local_files_only=True,
            )
        try:
            segments, info = model.transcribe(
                io.BytesIO(raw),
                language=data.language,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            rows = [
                {
                    "start_ms": max(0, round(s.start * 1000)),
                    "end_ms": min(round(duration * 1000), round(s.end * 1000)),
                    "text": s.text.strip(),
                    "avg_logprob": round(s.avg_logprob, 4),
                    "no_speech_prob": round(s.no_speech_prob, 4),
                }
                for s in segments
                if s.text.strip()
            ]
        except ValueError as exc:
            raise HTTPException(422, "Unsupported language or invalid audio") from exc
    return {
        "model_key": KEY,
        "language": info.language,
        "segments": rows,
        "duration_ms": round(duration * 1000),
    }
