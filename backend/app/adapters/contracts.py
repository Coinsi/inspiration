"""策略接口契约(对照 04-接口契约 第 4 章)。

M0 仅定义契约与注册中心;具体实现(即梦/解析/拆解/一致性)在 M2-M4 落地。
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Literal

from pydantic import BaseModel, Field

from app.registry.base import Registry

# ── 共享数据结构 ──


class Capabilities(BaseModel):
    strict_parameters: bool = False
    modalities: set[Literal["image", "video"]] = Field(default_factory=set)
    features: set[str] = Field(default_factory=set)  # img2img/inpaint/controlnet/reference/lora
    max_reference_images: int = 0
    max_video_seconds: float | None = None
    param_schema: dict = Field(default_factory=dict)


class ReferenceImage(BaseModel):
    blob_hash: str
    role: str = "ref"
    # 由服务层在提交前物化(blob → base64 data URI),供应商据此发送;不入库快照(避免膨胀)
    data_url: str | None = None


class GenerationRequest(BaseModel):
    request_type: Literal["image", "video"]
    prompt: str
    negative: str | None = None
    references: list[ReferenceImage] = Field(default_factory=list)
    mask: ReferenceImage | None = None
    seed: int | None = None
    params: dict = Field(default_factory=dict)
    provider_params: dict = Field(default_factory=dict)
    count: int = 1


class CostEstimate(BaseModel):
    points: float
    detail: dict = Field(default_factory=dict)


class JobHandle(BaseModel):
    external_job_id: str
    raw: dict = Field(default_factory=dict)


class Output(BaseModel):
    data: bytes | None = None
    url: str | None = None
    type: Literal["image", "video"] = "image"
    meta: dict = Field(default_factory=dict)


class GenerationResult(BaseModel):
    status: Literal["running", "succeeded", "failed"]
    outputs: list[Output] = Field(default_factory=list)
    cost_raw: dict | None = None
    error: str | None = None


# ── 策略接口 ──


class GenerationProvider(ABC):
    name: str

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def estimate_cost(self, req: GenerationRequest) -> CostEstimate: ...

    @abstractmethod
    def submit(self, req: GenerationRequest) -> JobHandle: ...

    @abstractmethod
    def poll(self, handle: JobHandle) -> GenerationResult: ...

    def cancel(self, handle: JobHandle) -> None:  # 可选
        ...

    def verify_callback(self, payload: dict, headers: dict) -> bool:  # 可选(预留 webhook)
        return False


class Chapter(BaseModel):
    ordinal: int
    title: str | None = None
    content: str = ""


class NovelDocument(BaseModel):
    title: str
    chapters: list[Chapter] = Field(default_factory=list)


class NovelParser(ABC):
    name: str

    @abstractmethod
    def can_handle(self, filename: str, mime: str) -> bool: ...

    @abstractmethod
    def parse(self, file: BinaryIO) -> NovelDocument: ...


# ── AI 辅助拆解 ──


class SceneSuggestion(BaseModel):
    title: str
    summary: str = ""
    body: str = ""
    source_chapter_ordinal: int | None = None


class ShotSuggestion(BaseModel):
    title: str
    description: str = ""


class StoryboardShotSuggestion(BaseModel):
    """AI 拆分镜:镜头 + 分镜规格建议(各 *_ 字段为前后端统一的稳定 key)。"""

    title: str = ""
    description: str = ""
    shot_size: str = ""
    camera_angle: str = ""
    camera_move: str = ""
    transition: str = ""
    aspect_ratio: str = ""
    pacing: str = ""
    duration_sec: float | None = None
    dialogue: str = ""


class EntityDraft(BaseModel):
    type: Literal["character", "prop", "location", "costume", "vehicle"]
    name: str
    summary: str = ""


# 剧本正文块类型(typed blocks);AI 与编辑器共用的稳定集合
SCRIPT_BLOCK_TYPES = (
    "scene_heading",
    "action",
    "character",
    "dialogue",
    "parenthetical",
    "transition",
)

# 设定(故事圣经)预置分类;category 存自由字符串,此集合仅为预置引导,可扩展
SETTING_CATEGORIES = (
    "world",
    "power_system",
    "faction",
    "character",
    "location",
    "item",
    "glossary",
    "timeline",
)


class SettingDraft(BaseModel):
    """单章提取出的一条设定。name 与已有设定同名表示「补充更新」(content 应为合并后的完整版)。"""

    category: str
    name: str
    content: str = ""


class ScriptBlock(BaseModel):
    """剧本正文的一个块(场景头/动作/角色/对白/括号提示/转场)。"""

    block_type: Literal[
        "scene_heading", "action", "character", "dialogue", "parenthetical", "transition"
    ]
    text: str = ""


class DecompositionStrategy(ABC):
    name: str

    @abstractmethod
    def suggest_scenes(self, chapter_text: str) -> list[SceneSuggestion]: ...

    @abstractmethod
    def suggest_shots(self, scene_text: str) -> list[ShotSuggestion]: ...

    @abstractmethod
    def extract_entities(self, text: str) -> list[EntityDraft]: ...

    def draft_prompt(self, scene_text: str, shot: ShotSuggestion) -> str:
        return f"{shot.title}. {shot.description}".strip()

    def suggest_storyboard_shots(self, scene_text: str) -> list[StoryboardShotSuggestion]:
        """默认实现:基于 suggest_shots,仅给标题/描述(无规格);云 LLM 会覆盖出完整规格。"""
        return [
            StoryboardShotSuggestion(title=s.title, description=s.description)
            for s in self.suggest_shots(scene_text)
        ]

    def extract_settings(self, chapter_text: str, existing: list[dict]) -> list[SettingDraft]:
        """从一章正文提取设定(故事圣经条目)。

        existing 是已有设定的轻量名录 [{category, name, content(截断)}],用于增量合并:
        返回的同名条目会整条覆盖原 content(应给出合并补全后的完整版),新名字则新建。
        默认实现返回空(mock/cloud_llm 覆盖)。
        """
        return []

    def merge_setting_versions(self, conflicts: list[dict]) -> list[SettingDraft]:
        """并发提取的冲突合并:同一条设定出现多个版本时,融合为一个完整版。

        conflicts: [{category, name, versions: [content, ...]}]
        默认实现:取最长版本(离线可用);云 LLM 覆盖为真正的语义融合。
        """
        return [
            SettingDraft(
                category=c["category"],
                name=c["name"],
                content=max((c.get("versions") or [""]), key=len),
            )
            for c in conflicts
        ]

    def adapt_chapter_to_script(self, chapter_text: str, context: str = "") -> list[ScriptBlock]:
        """把章节正文改编为剧本正文块。

        context 为「已确立的故事设定」文本(S3:由设定库组装注入),改编须遵守;可为空。
        默认实现:基于 suggest_scenes,把每个场次渲染成「场景头 + 动作」两块(离线可用);
        云 LLM 会覆盖出完整的对白/角色/转场等专业剧本块。
        """
        blocks: list[ScriptBlock] = []
        for i, sc in enumerate(self.suggest_scenes(chapter_text), 1):
            heading = (sc.title or f"SCENE {i}").strip()
            blocks.append(ScriptBlock(block_type="scene_heading", text=heading))
            body = (sc.body or sc.summary or "").strip()
            if body:
                blocks.append(ScriptBlock(block_type="action", text=body))
        return blocks


# ── 一致性策略 ──


class ConsistencyContext(BaseModel):
    """从资产视觉身份提炼的上下文(由服务层构建,策略不直接碰 ORM)。"""

    asset_name: str
    prompt_fragment_text: str | None = None
    lora_ref: dict | None = None
    reference_set: dict | None = None


class ConsistencyStrategy(ABC):
    name: str

    @abstractmethod
    def prompt_parts(self, ctx: ConsistencyContext) -> list[str]:
        """该资产应注入最终提示词的片段(M3)。"""

    def generation_extras(self, ctx: ConsistencyContext) -> dict:
        """生成期附加(参考图/LoRA 配置),M4 生成时消费。"""
        return {}


# ── 贯穿式 AI 对话助手引擎 ──


class AssistProposal(BaseModel):
    """助手对一条用户指令的回应:自然语言回复 + 结构化修改操作(ops)。

    ops 的具体形态由目标对象适配器定义并校验;引擎只负责产出候选,不直接碰 ORM。
    needs_clarification=True 表示指令不明确,助手在 reply 里反问,ops 应为空。
    """

    reply: str = ""
    summary: str = ""
    ops: list[dict] = Field(default_factory=list)
    needs_clarification: bool = False


class AssistEngine(ABC):
    name: str

    @abstractmethod
    def propose(
        self,
        *,
        object_desc: str,
        ops_help: str,
        state: dict,
        history: list[dict],
        instruction: str,
    ) -> AssistProposal: ...


# ── 注册中心实例(各扩展点一个) ──
generation_registry: Registry[GenerationProvider] = Registry("generation")
parser_registry: Registry[NovelParser] = Registry("parser")
decomposition_registry: Registry[DecompositionStrategy] = Registry("decomposition")
consistency_registry: Registry[ConsistencyStrategy] = Registry("consistency")
assist_registry: Registry[AssistEngine] = Registry("assist")
