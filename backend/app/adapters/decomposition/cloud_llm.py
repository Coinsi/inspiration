"""云 LLM 拆解策略(OpenAI 兼容 Chat Completions)。

决策 D2:AI 辅助拆解以云 API 为主。本适配器读取 settings.llm_*(或后续可由
provider_config 注入)。未配置 API Key 时调用即报错,提示需要 Key。
通过共享 LiteLLM SDK 流式调用，供应商与密钥按请求隔离。
"""

import json
from pydantic import ValidationError

from app.adapters.contracts import (
    SCRIPT_BLOCK_TYPES,
    SETTING_CATEGORIES,
    DecompositionStrategy,
    EntityDraft,
    SceneSuggestion,
    ScriptBlock,
    SettingDraft,
    ShotSuggestion,
    ShotSubject,
    StoryboardShotSuggestion,
    decomposition_registry,
)
from app.core.config import settings
from app.core.errors import ProviderError

_SHOT_SIZES = [
    "extreme_long",
    "long",
    "full",
    "medium",
    "medium_close",
    "close_up",
    "extreme_close_up",
]
_CAMERA_ANGLES = ["eye_level", "high", "low", "birds_eye", "over_shoulder"]
_CAMERA_MOVES = ["static", "push_in", "pull_out", "pan", "tilt", "tracking", "crane", "handheld"]
_TRANSITIONS = ["cut", "fade_in", "fade_out", "dissolve", "wipe", "push"]
_ASPECT_RATIOS = ["16:9", "2.35:1", "4:3", "1:1", "9:16"]
_PACINGS = ["slow", "normal", "fast"]


def _shot_subjects(raw):
    subjects = []
    for item in raw if isinstance(raw, list) else []:
        try:
            subjects.append(ShotSubject.model_validate(item))
        except ValidationError:
            continue
    return subjects


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _chat(system, user, base_url, api_key, model, *, sdk_provider="openai"):
    from app.adapters.model_sdk import chat

    # Keep the model's sampling default: reasoning models may reject overrides.
    return chat(system, user, base_url, api_key, model, provider=sdk_provider)[0]


def _parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProviderError("模型返回的内容无法解析，请重试该条目") from exc
    if not isinstance(data, dict):
        raise ProviderError("模型返回的数据格式有误，请重试该条目")
    return data


def _rows(text, key):
    rows = _parse_json(text).get(key)
    if not isinstance(rows, list):
        raise ProviderError("模型回复中缺少请求的列表，请重试该条目")
    return rows


@decomposition_registry.register("cloud_llm")
class CloudLLMDecomposer(DecompositionStrategy):
    name = "cloud_llm"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        sdk_provider="openai",
        **_,
    ):
        # 优先用项目设置注入的配置,缺省回落到环境变量
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.sdk_provider = sdk_provider

    def _ask(self, system: str, user: str) -> str:
        return _chat(
            system, user, self.base_url, self.api_key, self.model, sdk_provider=self.sdk_provider
        )

    def suggest_scenes(self, chapter_text: str) -> list[SceneSuggestion]:
        out = self._ask(
            "你是影视编剧助手。把小说章节切分为若干『场次』,只输出 JSON:"
            '{"scenes":[{"title":"","summary":"","body":""}]}',
            chapter_text,
        )
        scenes = [SceneSuggestion(**s) for s in _rows(out, "scenes")]
        if not scenes and chapter_text.strip():
            raise ProviderError("模型没有返回场次，请重试该章")
        return scenes

    def suggest_shots(self, scene_text: str) -> list[ShotSuggestion]:
        out = self._ask(
            "你是分镜师。把一场戏拆为若干『镜头』,只输出 JSON:"
            '{"shots":[{"title":"","description":""}]}',
            scene_text,
        )
        return [ShotSuggestion(**s) for s in _rows(out, "shots")]

    def suggest_storyboard_shots(self, scene_text: str) -> list[StoryboardShotSuggestion]:
        out = self._ask(
            "你是专业影视分镜师。把给定的一场戏拆成若干个电影镜头,从导演视角决定怎么一个镜头一个镜头地拍。"
            "只输出 JSON,不要解释。每个镜头都要给全下列字段(尽量都填,实在不适用才留空字符串):"
            "title 为简短镜头名，description 为剧情摘要。still_prompt 写成可独立出图的定格画面，"
            "交代可见人物及当期造型、姿态与视线、人物相对位置、前中后景、景别、光源材质和色调。"
            "motion_prompt 描写从该帧开始的动作起止、镜头移动、节奏和与相邻镜头的连续性。"
            "遵守原文与既定造型；缺少依据时保留创作空间。subjects 列出镜中可见的人物、地点、道具，"
            "含 name(可确定的本名)、type(character/location/prop/costume/vehicle/style)、"
            "aspects(本镜使用 face/wardrobe/layout/light/appearance 中的哪些)、look(有依据的时期造型，否则留空)、"
            "evidence(从剧本或原章逐字摘录支持其入镜的短句)。仅被台词提及的人物不计入；过肩镜头可以只引用衣着。"
            f"shot_size(景别,必须从这些值里选一个:{'/'.join(_SHOT_SIZES)})、"
            f"camera_angle(机位角度,必须从:{'/'.join(_CAMERA_ANGLES)})、"
            f"camera_move(运镜,必须从:{'/'.join(_CAMERA_MOVES)})、"
            f"transition(与下一镜的转场,必须从:{'/'.join(_TRANSITIONS)})、"
            f"aspect_ratio(画面比例,必须从:{'/'.join(_ASPECT_RATIOS)})、"
            f"pacing(节奏,必须从:{'/'.join(_PACINGS)})、"
            "duration_sec(预计时长秒,数字)、dialogue(该镜头的对白或旁白,没有就空字符串)。"
            '输出格式:{"shots":[{"title":"","description":"","shot_size":"","camera_angle":"",'
            '"camera_move":"","transition":"","aspect_ratio":"","pacing":"","duration_sec":3,"dialogue":"",'
            '"still_prompt":"","motion_prompt":"","subjects":[]}]}',
            scene_text,
        )

        def pick(v, allowed):
            return v if v in allowed else ""

        res: list[StoryboardShotSuggestion] = []
        for it in _rows(out, "shots"):
            if not isinstance(it, dict):
                continue
            res.append(
                StoryboardShotSuggestion(
                    title=str(it.get("title", "") or ""),
                    description=str(it.get("description", "") or ""),
                    shot_size=pick(it.get("shot_size", ""), _SHOT_SIZES),
                    camera_angle=pick(it.get("camera_angle", ""), _CAMERA_ANGLES),
                    camera_move=pick(it.get("camera_move", ""), _CAMERA_MOVES),
                    transition=pick(it.get("transition", ""), _TRANSITIONS),
                    aspect_ratio=pick(it.get("aspect_ratio", ""), _ASPECT_RATIOS),
                    pacing=pick(it.get("pacing", ""), _PACINGS),
                    duration_sec=_num(it.get("duration_sec")),
                    dialogue=str(it.get("dialogue", "") or ""),
                    still_prompt=str(it.get("still_prompt") or ""),
                    motion_prompt=str(it.get("motion_prompt") or ""),
                    subjects=_shot_subjects(it.get("subjects")),
                )
            )
        return res

    def extract_entities(self, text: str) -> list[EntityDraft]:
        out = self._ask(
            "你是资产管理助手。从文本抽取角色/道具/场景实体,type 取 "
            "character/prop/location/costume/vehicle,只输出 JSON:"
            "人物使用原文明示的名字。无姓名者用场次或可区分的身份命名，代词不足以证明身份。"
            "每项 evidence 原样摘录能定位该实体的短句。"
            '{"entities":[{"type":"","name":"","summary":"","evidence":""}]}',
            text,
        )
        drafts = [EntityDraft(**e) for e in _rows(out, "entities")]
        source = "".join(text.split())
        for draft in drafts:
            if "".join(draft.evidence.split()) not in source:
                draft.evidence = ""
        return drafts

    def extract_settings(self, chapter_text: str, existing: list[dict]) -> list[SettingDraft]:
        existing_brief = json.dumps(
            [
                {
                    "category": e.get("category"),
                    "name": e.get("name"),
                    "content": (e.get("content") or "")[:120],
                }
                for e in existing
            ][:120],
            ensure_ascii=False,
        )
        out = self._ask(
            "你是「故事圣经」管理员,负责通读小说沉淀全书设定。从给定章节中提取值得记录的设定条目。"
            f"category 必须从这些值里选:{'/'.join(SETTING_CATEGORIES)}"
            "(world=世界观背景, power_system=力量/规则体系, faction=势力/组织, character=人物, "
            "location=地点, item=物品/道具, glossary=术语, timeline=大事记)。"
            "下面给你「已有设定名录」:如果本章信息能补充某条已有设定,返回**同 category 同 name** 的条目,"
            "content 给出【合并已有信息与新信息后的完整版】(不是只写新增部分);全新设定则用新 name。"
            "无关紧要的琐碎信息不要收录;人物条目应包含外貌/性格/能力/目标等可累积的要点。"
            "同名、代词或衣着相似不足以认定同一人。无姓名且身份不明时，用能区分人物的场次及身份命名。"
            'chapter_fact 只写本章新增事实，quote 逐字摘录支持这一事实的原文短句。只输出 JSON:{"settings":[{"category":"","name":"","content":"","chapter_fact":"","quote":""}]}\n\n'
            f"【已有设定名录】\n{existing_brief}",
            chapter_text,
        )
        allowed = set(SETTING_CATEGORIES)
        res: list[SettingDraft] = []
        for it in _rows(out, "settings"):
            if not isinstance(it, dict):
                continue
            cat = str(it.get("category", "") or "")
            name = str(it.get("name", "") or "").strip()
            content = str(it.get("content", "") or "").strip()
            if cat not in allowed or not name or not content:
                continue
            quote = str(it.get("quote") or "").strip()
            res.append(
                SettingDraft(
                    category=cat,
                    name=name,
                    content=content,
                    chapter_fact=str(it.get("chapter_fact") or ""),
                    quote=quote if quote and quote in chapter_text else "",
                )
            )
        return res

    def merge_setting_versions(self, conflicts: list[dict]) -> list[SettingDraft]:
        if not conflicts:
            return []
        payload = json.dumps(
            [
                {
                    "category": c["category"],
                    "name": c["name"],
                    "versions": (c.get("versions") or [])[:6],
                }
                for c in conflicts
            ][:40],
            ensure_ascii=False,
        )
        out = self._ask(
            "你是「故事圣经」管理员。下面每个条目有同一设定的多个版本(来自并行阅读的不同章节),"
            "把每个条目的所有版本**融合成一个不丢信息、不自相矛盾的完整版**(冲突处以更具体/更晚出现的信息为准)。"
            '只输出 JSON:{"settings":[{"category":"","name":"","content":""}]},条目数与输入一致。',
            payload,
        )
        res: list[SettingDraft] = []
        merged = {
            (str(it.get("category")), str(it.get("name", "")).strip()): str(
                it.get("content", "") or ""
            )
            for it in _rows(out, "settings")
            if isinstance(it, dict)
        }
        for c in conflicts:
            key = (c["category"], c["name"])
            content = merged.get(key) or "\n".join(dict.fromkeys(c.get("versions") or []))
            res.append(SettingDraft(category=c["category"], name=c["name"], content=content))
        return res

    def adapt_chapter_to_script(self, chapter_text: str, context: str = "") -> list[ScriptBlock]:
        ctx_part = (
            f"【已确立的故事设定(改编必须遵守,人物言行、世界规则不得违背)】\n{context}\n\n"
            if context
            else ""
        )
        out = self._ask(
            f"{ctx_part}"
            "你是专业影视编剧。把给定的小说章节改编为标准剧本正文,输出为有序的剧本块序列。"
            "只输出 JSON,不要解释。每个块有 type 和 text 两个字段;type 必须从这些值里选一个:"
            f"{'/'.join(SCRIPT_BLOCK_TYPES)}。"
            "用法:scene_heading=场景头(如『INT. 咖啡馆 - 日』);action=动作/场景描述行;"
            "character=说话角色名(单独成块,大写);dialogue=该角色的对白;"
            "parenthetical=括号表演提示(如『(冷笑)』);transition=转场(如『CUT TO:』)。"
            "对白要在对应的 character 块之后。尽量还原戏剧冲突与对白,不要逐句照抄小说旁白。"
            '输出格式:{"blocks":[{"type":"scene_heading","text":""},{"type":"action","text":""},'
            '{"type":"character","text":""},{"type":"dialogue","text":""}]}',
            chapter_text,
        )
        allowed = set(SCRIPT_BLOCK_TYPES)
        res: list[ScriptBlock] = []
        for it in _rows(out, "blocks"):
            if not isinstance(it, dict):
                continue
            t = str(it.get("type", "") or "")
            if t not in allowed:
                continue
            text = str(it.get("text", "") or "").strip()
            if not text:
                continue
            res.append(ScriptBlock(block_type=t, text=text))
        if not res:
            raise ProviderError("模型没有返回有效的剧本内容，请重试该章")
        return res
