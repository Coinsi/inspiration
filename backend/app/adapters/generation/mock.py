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


@generation_registry.register("mock")
class MockProvider(GenerationProvider):
    name = "mock"

    def __init__(self, **kwargs):
        self._cfg = kwargs

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities={"image", "video"},
            features=set(),
            max_reference_images=0,
            max_video_seconds=10,
            param_schema={
                "duration": {"enum": [3, 5, 10]},
                "aspect_ratio": {"enum": ["16:9", "9:16", "1:1"]},
                "resolution": {"enum": ["720p"]},
            },
        )

    def estimate_cost(self, req: GenerationRequest) -> CostEstimate:
        unit = 50 if req.request_type == "video" else 10
        return CostEstimate(points=unit * req.count, detail={"unit": unit, "count": req.count})

    def submit(self, req: GenerationRequest) -> JobHandle:
        return JobHandle(
            external_job_id="mock-job",
            raw={"count": req.count, "type": req.request_type, "params": req.provider_params},
        )

    def poll(self, handle: JobHandle) -> GenerationResult:
        count = handle.raw.get("count", 1)
        typ = handle.raw.get("type", "image")
        from app.modules.generation.media_engine import mock_image, render

        outputs = []
        for i in range(count):
            data = mock_image(i)
            if typ == "video":
                params = handle.raw.get("params", {})
                data = render(
                    [
                        {
                            "blob_hash": "mock",
                            "output_type": "image",
                            "duration_ms": int(params.get("duration", 3)) * 1000,
                        }
                    ],
                    lambda _: data,
                    {"height": 720, "aspect_ratio": params.get("aspect_ratio", "16:9")},
                )
            outputs.append(Output(data=data, type=typ, meta={"variant": i, "simulation": True}))
        return GenerationResult(status="succeeded", outputs=outputs, cost_raw={"provider": "mock"})
