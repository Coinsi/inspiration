"""Mock 拆解策略:纯规则,无需外部 LLM。用于打通链路与离线验收。

切分逻辑:按空行分段;每段作为一个场次;场内按句号粗分镜头;
实体抽取用简单的引号/常见标记启发式(仅作占位,真实效果由云 LLM 提供)。
"""
import re

from app.adapters.contracts import (
    DecompositionStrategy,
    EntityDraft,
    SceneSuggestion,
    SettingDraft,
    ShotSuggestion,
    decomposition_registry,
)


@decomposition_registry.register("mock")
class MockDecomposer(DecompositionStrategy):
    name = "mock"

    def suggest_scenes(self, chapter_text: str) -> list[SceneSuggestion]:
        blocks = [b.strip() for b in re.split(r"\n\s*\n", chapter_text) if b.strip()]
        if not blocks:
            blocks = [chapter_text.strip() or "空场景"]
        scenes = []
        for i, b in enumerate(blocks, 1):
            summary = b[:30].replace("\n", " ")
            scenes.append(SceneSuggestion(title=f"场景 {i}:{summary}", summary=summary, body=b))
        return scenes

    def suggest_shots(self, scene_text: str) -> list[ShotSuggestion]:
        sentences = [s.strip() for s in re.split(r"[。!?\.!?]+", scene_text) if s.strip()]
        if not sentences:
            sentences = [scene_text.strip() or "空镜头"]
        return [
            ShotSuggestion(title=f"镜头 {i}", description=s[:60]) for i, s in enumerate(sentences, 1)
        ]

    def extract_entities(self, text: str) -> list[EntityDraft]:
        # 启发式:抽取「书名号/引号」内的短词作为道具/角色候选(占位)
        names = set(re.findall(r"[「『\"“]([^」』\"”]{1,8})[」』\"”]", text))
        drafts = [EntityDraft(type="prop", name=n, summary="自动抽取(待人工确认)") for n in names]
        return drafts[:20]

    def extract_settings(self, chapter_text: str, existing: list[dict]) -> list[SettingDraft]:
        # 启发式占位:书名号/引号内短词 → 术语表条目(跳过已有同名;真实效果由云 LLM 提供)
        known = {(e.get("category"), e.get("name")) for e in existing}
        names = set(re.findall(r"[「『《\"“]([^」』》\"”]{2,10})[」』》\"”]", chapter_text))
        out = [
            SettingDraft(category="glossary", name=n, content="自动抽取的术语(待补充解释)")
            for n in names
            if ("glossary", n) not in known
        ]
        return out[:10]
