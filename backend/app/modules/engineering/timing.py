"""Exact rational edit-time maps. No frame sampling or implicit time stretching."""

from fractions import Fraction
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.modules.engineering.native import Span, Strict, Time


def value(time):
    return Fraction(time.numerator, time.denominator)


def encoded(time):
    time = Fraction(time)
    return {"numerator": time.numerator, "denominator": time.denominator}


class Rate(Strict):
    numerator: int = Field(strict=True)
    denominator: int = Field(gt=0, strict=True)


class Segment(Strict):
    id: UUID
    local: Span
    source_start: Time
    rate: Rate
    join: Literal["cut", "continuous"] = "cut"
    hold: bool = False


class TimeMap(Strict):
    duration: Time
    source_domain: Span
    audio_policy: Literal["none", "mute", "stretch", "resample", "reverse"]
    segments: list[Segment] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def check(self):
        end, prior_source = Fraction(0), None
        seen = set()
        low, high = value(self.source_domain.start), value(self.source_domain.end)
        for segment in self.segments:
            start, stop = value(segment.local.start), value(segment.local.end)
            rate, source = value(segment.rate), value(segment.source_start)
            if segment.id in seen or start != end:
                raise ValueError("时间映射必须按顺序连续覆盖，每段身份唯一")
            seen.add(segment.id)
            if (not rate) != segment.hold:
                raise ValueError("零速率必须明确停帧，非零速率不能标记停帧")
            if segment.join == "continuous" and (prior_source is None or source != prior_source):
                raise ValueError("连续映射的源边界不一致")
            source_end = source + (stop - start) * rate
            if min(source, source_end) < low or max(source, source_end) > high:
                raise ValueError("时间映射超出声明的源域")
            if not rate and source == high:
                raise ValueError("停帧不能位于源域右开边界")
            if not rate and self.audio_policy not in {"none", "mute"}:
                raise ValueError("停帧声音必须明确静音")
            if rate < 0 and self.audio_policy not in {"none", "mute", "reverse"}:
                raise ValueError("负速率声音必须明确倒放或静音")
            end, prior_source = stop, source_end
        if end != value(self.duration) or end <= 0:
            raise ValueError("时间映射未完整覆盖使用时长")
        return self


def source_at(mapping: TimeMap, local: Fraction):
    for segment in mapping.segments:
        if value(segment.local.start) <= local < value(segment.local.end):
            return value(segment.source_start) + (local - value(segment.local.start)) * value(
                segment.rate
            )
    raise ValueError("位置不在使用区间内")


def inverse(mapping: TimeMap, source: Fraction):
    """Return every occurrence, including hold intervals; never choose the first."""
    result = []
    for segment in mapping.segments:
        start, end = value(segment.local.start), value(segment.local.end)
        rate, origin = value(segment.rate), value(segment.source_start)
        if rate:
            local = start + (source - origin) / rate
            if start <= local < end:
                result.append({"segment_id": str(segment.id), "point": encoded(local)})
        elif source == origin:
            result.append(
                {
                    "segment_id": str(segment.id),
                    "range": {"start": encoded(start), "end": encoded(end)},
                }
            )
    return result


def affected_ranges(
    mapping: TimeMap,
    source_start: Fraction,
    source_end: Fraction,
    *,
    start_closed=True,
    end_closed=False,
):
    """Exact inverse of a half-open source interval, preserving reversed boundaries."""
    if source_start > source_end or (
        source_start == source_end and not (start_closed and end_closed)
    ):
        raise ValueError("变化区间必须非空")
    result = []
    for segment in mapping.segments:
        start, end = value(segment.local.start), value(segment.local.end)
        origin, rate = value(segment.source_start), value(segment.rate)
        if not rate:
            if (origin > source_start or (origin == source_start and start_closed)) and (
                origin < source_end or (origin == source_end and end_closed)
            ):
                result.append(
                    {
                        "segment_id": str(segment.id),
                        "start": encoded(start),
                        "end": encoded(end),
                        "start_closed": True,
                        "end_closed": False,
                    }
                )
            continue
        a, b = start + (source_start - origin) / rate, start + (source_end - origin) / rate
        low, high = min(a, b), max(a, b)
        clipped_low, clipped_high = max(start, low), min(end, high)
        lower_closed = (start_closed if rate > 0 else end_closed) if clipped_low == low else True
        upper_closed = (end_closed if rate > 0 else start_closed) if clipped_high == high else False
        if clipped_high == end:
            upper_closed = False
        if clipped_low < clipped_high or (
            clipped_low == clipped_high and lower_closed and upper_closed
        ):
            result.append(
                {
                    "segment_id": str(segment.id),
                    "start": encoded(clipped_low),
                    "end": encoded(clipped_high),
                    "start_closed": lower_closed,
                    "end_closed": upper_closed,
                }
            )
    return result
