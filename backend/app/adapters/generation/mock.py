"""Mock 生成供应商:离线即时产出占位图/视频,用于打通编排与验收。"""
from app.adapters.contracts import (
    Capabilities,
    CostEstimate,
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
    JobHandle,
    Output,
    generation_registry,
)

# 最小合法 1x1 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)


@generation_registry.register("mock")
class MockProvider(GenerationProvider):
    name = "mock"

    def __init__(self, **kwargs):
        self._cfg = kwargs

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities={"image", "video"},
            features={"reference"},
            max_reference_images=4,
            max_video_seconds=10,
            param_schema={},
        )

    def estimate_cost(self, req: GenerationRequest) -> CostEstimate:
        unit = 50 if req.request_type == "video" else 10
        return CostEstimate(points=unit * req.count, detail={"unit": unit, "count": req.count})

    def submit(self, req: GenerationRequest) -> JobHandle:
        return JobHandle(external_job_id="mock-job", raw={"count": req.count, "type": req.request_type})

    def poll(self, handle: JobHandle) -> GenerationResult:
        count = handle.raw.get("count", 1)
        typ = handle.raw.get("type", "image")
        outputs = [
            # 追加索引字节使每个变体内容哈希不同(CAS 去重下仍为独立 blob)
            Output(data=_PNG + str(i).encode(), type=typ, meta={"variant": i})
            for i in range(count)
        ]
        return GenerationResult(status="succeeded", outputs=outputs, cost_raw={"provider": "mock"})
