import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(ge=-500, le=500, allow_inf_nan=False)]
Vector = tuple[Number, Number, Number]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(Strict):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["person", "box", "sphere", "wall"] = "person"
    position: Vector = (0, 0, 0)
    rotation: Number = 0
    scale: float = Field(default=1, ge=0.1, le=20, allow_inf_nan=False)
    color: Color = "#c4a574"
    pose: Literal["standing", "sitting"] = "standing"


class Camera(Strict):
    position: Vector = (6, 4, 8)
    target: Vector = (0, 1, 0)
    fov: float = Field(default=45, ge=15, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def distance(self):
        if sum((a - b) ** 2 for a, b in zip(self.position, self.target, strict=True)) < 0.01:
            raise ValueError("机位与目标不能重合")
        return self


class Lighting(Strict):
    position: Vector = (4, 8, 4)
    intensity: float = Field(default=3, ge=0, le=10, allow_inf_nan=False)
    ambient: float = Field(default=0.8, ge=0, le=3, allow_inf_nan=False)
    color: Color = "#fff0d6"


Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class CameraKeyframe(Strict):
    id: uuid.UUID
    at: Fraction
    camera: Camera
    hold: Fraction = 0
    ease: Literal["smooth", "linear"] = "smooth"


class Document(Strict):
    objects: list[Actor] = Field(default_factory=list, max_length=80)
    camera: Camera = Field(default_factory=Camera)
    end_camera: Camera = Field(default_factory=lambda: Camera(position=(4, 2, 5)))
    camera_keyframes: list[CameraKeyframe] = Field(default_factory=list, max_length=10)
    camera_hold: Fraction = 0
    camera_ease: Literal["smooth", "linear"] = "smooth"
    lighting: Lighting = Field(default_factory=Lighting)
    background: Color = "#252b32"
    ground: Color = "#737d7e"
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    duration: float = Field(default=5, ge=1, le=15, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique(self):
        if len({o.id for o in self.objects}) != len(self.objects):
            raise ValueError("重复的场景对象")
        if len({k.id for k in self.camera_keyframes}) != len(self.camera_keyframes):
            raise ValueError("重复的运镜关键帧")
        points = [(0, self.camera_hold, self.camera)]
        points.extend((k.at, k.hold, k.camera) for k in self.camera_keyframes)
        points.append((1, 0, self.end_camera))
        for (at, hold, camera), (next_at, _, next_camera) in zip(
            points[:-1], points[1:], strict=True
        ):
            if next_at - at - hold < 0.01 - 1e-9:
                raise ValueError("机位须按时间排序，停留后至少留出总时长的1%用于运镜")
            # Avoid a camera passing through its look-at target during interpolation.
            start = [a - b for a, b in zip(camera.position, camera.target, strict=True)]
            end = [a - b for a, b in zip(next_camera.position, next_camera.target, strict=True)]
            delta = [b - a for a, b in zip(start, end, strict=True)]
            length = sum(x * x for x in delta)
            t = (
                max(0, min(1, -sum(a * b for a, b in zip(start, delta, strict=True)) / length))
                if length
                else 0
            )
            if sum((a + t * b) ** 2 for a, b in zip(start, delta, strict=True)) < 0.01 - 1e-9:
                raise ValueError("运镜路径经过取景目标，请调整机位或添加绕行机位")
        return self


class Create(Strict):
    name: str = Field(default="未命名预演", min_length=1, max_length=120)


class Revision(Strict):
    revision: int = Field(ge=0)


class Save(Revision):
    name: str = Field(min_length=1, max_length=120)
    document: Document
