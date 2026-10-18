"""Native document contracts. Persistence support is distinct from execution support.

Time is rational seconds, ranges are end-exclusive, and all sources are named
fixed-revision inputs. No provider, agent SDK, URL or local path is an identity.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Time(Strict):
    numerator: int = Field(ge=0, strict=True)
    denominator: int = Field(gt=0, strict=True)


class Span(Strict):
    start: Time
    end: Time

    @model_validator(mode="after")
    def ordered(self):
        if (
            self.end.numerator * self.start.denominator
            <= self.start.numerator * self.end.denominator
        ):
            raise ValueError("时间范围必须为非空的左闭右开区间")
        return self


class Reference(Strict):
    id: UUID
    revision: int = Field(ge=1, strict=True)


class Input(Strict):
    target: Reference
    accepts: str = Field(min_length=1, max_length=120)


class Transform(Strict):
    x: float = 0
    y: float = 0
    scale_x: float = 1
    scale_y: float = 1
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)


class Layer(Strict):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1)
    mask: str | None = None
    enabled: bool = True
    transform: Transform = Field(default_factory=Transform)
    blend: Literal["normal", "multiply", "screen", "overlay"] = "normal"


class Canvas(Strict):
    width: int = Field(gt=0, le=32768)
    height: int = Field(gt=0, le=32768)
    color_space: Literal["srgb", "linear-srgb", "rec709"] = "srgb"
    alpha: Literal["straight", "opaque"] = "straight"


class ImageDocument(Strict):
    canvas: Canvas
    layers: list[Layer] = Field(default_factory=list, max_length=2000)


class AudioClip(Strict):
    id: UUID
    source: str
    source_range: Span
    start: Time
    gain_db: float = Field(default=0, ge=-120, le=24)
    muted: bool = False


class AudioTrack(Strict):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    clips: list[AudioClip] = Field(default_factory=list, max_length=2000)
    gain_db: float = Field(default=0, ge=-120, le=24)
    muted: bool = False


class AudioDocument(Strict):
    sample_rate: Literal[44100, 48000, 96000] = 48000
    channels: Literal[1, 2] = 2
    tracks: list[AudioTrack] = Field(default_factory=list, max_length=128)


class TimedLayer(Layer):
    active: Span
    source_start: Time


class CompositionDocument(Strict):
    canvas: Canvas
    duration: Time
    frame_rate: Time
    layers: list[TimedLayer] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def timing(self):
        if not self.duration.numerator or not self.frame_rate.numerator:
            raise ValueError("时长与帧率必须大于零")
        for layer in self.layers:
            end = layer.active.end
            if (
                end.numerator * self.duration.denominator
                > self.duration.numerator * end.denominator
            ):
                raise ValueError("图层超出合成时长")
        return self


MODELS = {
    "image.document": ImageDocument,
    "audio.document": AudioDocument,
    "composition.document": CompositionDocument,
}


def validate(kind, inputs, payload):
    document = MODELS[kind].model_validate(payload)
    validate_inputs(inputs, document)
    return document


def validate_inputs(inputs, document):
    links = {key: Input.model_validate(value) for key, value in inputs.items()}
    ids = set()

    def identity(value):
        if value in ids:
            raise ValueError("文档实例标识重复")
        ids.add(value)

    def source(name, allowed):
        if name not in links or links[name].accepts not in allowed:
            raise ValueError("实例缺少兼容的固定输入")

    if isinstance(document, AudioDocument):
        for track in document.tracks:
            identity(track.id)
            for clip in track.clips:
                identity(clip.id)
                source(clip.source, {"resource", "audio.document"})
    else:
        for layer in document.layers:
            identity(layer.id)
            allowed = {"resource", "image.document"}
            if isinstance(document, CompositionDocument):
                allowed.add("composition.document")
            source(layer.source, allowed)
            if layer.mask is not None:
                source(layer.mask, {"resource", "image.document"})
