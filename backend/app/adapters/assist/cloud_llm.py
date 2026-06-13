"""云 LLM 助手引擎(OpenAI 兼容 Chat Completions)。

把"对象描述 + 合法操作说明 + 当前状态 + 对话历史 + 用户指令"组织成提示词,
要求模型只输出结构化 JSON {reply, summary, ops, needs_clarification}。

设计要点(把用户的修改做"准、稳、可回退"):
- 模型只产出**最小、定位精确**的 ops(引用真实的 block id),不重写整篇;
- 指令不明确时反问(needs_clarification),而不是乱猜;
- 严格 JSON,便于服务层逐条校验后再 dry-run 预览。
"""
import json
import urllib.request

from app.adapters.contracts import AssistEngine, AssistProposal, assist_registry
from app.core.config import settings
from app.core.errors import ProviderError

_SYSTEM = """你是「Inspiration」影视创作平台的 AI 编辑助手。用户在某个对象的侧边栏与你对话,要求你修改该对象。

你的工作方式(非常重要):
1. 你不是直接重写整段文字,而是产出一组**结构化编辑操作(ops)**,由系统精确地应用到对象上。
2. 每个操作都要**定位精确**:引用对象当前状态里真实存在的 id;改动**尽量小**,只动用户要求改的部分,不要动其它内容。
3. 如果用户的指令含糊、缺少必要信息,**不要乱猜**:把 needs_clarification 设为 true,在 reply 里用一句话反问,ops 留空。
4. reply 用中文,简洁地告诉用户你做了什么或你的疑问;summary 是这次改动的极简概括(便于展示)。

下面给你这个对象的说明、它支持的操作清单、它的当前状态、以及你和用户的对话历史。
只输出 JSON,不要任何解释或 markdown:
{"reply":"","summary":"","needs_clarification":false,"ops":[]}"""


def _chat(system: str, user: str, base_url: str, api_key: str | None, model: str) -> str:
    if not api_key:
        raise ProviderError("未配置 LLM API Key,无法使用 AI 助手(请在项目设置中配置,或用 mock 引擎)")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"云 LLM 调用失败:{exc}") from exc


@assist_registry.register("cloud_llm")
class CloudLLMAssist(AssistEngine):
    name = "cloud_llm"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, **_):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model

    def propose(self, *, object_desc, ops_help, state, history, instruction, **_) -> AssistProposal:
        hist = "\n".join(
            f"{m.get('role')}: {m.get('content', '')}" for m in (history or [])[-8:]
        )
        user = (
            f"【对象说明】\n{object_desc}\n\n"
            f"【支持的操作(ops)】\n{ops_help}\n\n"
            f"【对象当前状态(JSON)】\n{json.dumps(state, ensure_ascii=False)[:8000]}\n\n"
            f"【最近对话】\n{hist or '(无)'}\n\n"
            f"【用户本次指令】\n{instruction}"
        )
        raw = _chat(_SYSTEM, user, self.base_url, self.api_key, self.model)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return AssistProposal(reply="（助手返回的内容无法解析,请重试或换种说法）", needs_clarification=True)
        ops = data.get("ops") or []
        if not isinstance(ops, list):
            ops = []
        return AssistProposal(
            reply=str(data.get("reply", "") or ""),
            summary=str(data.get("summary", "") or ""),
            ops=[o for o in ops if isinstance(o, dict)],
            needs_clarification=bool(data.get("needs_clarification", False)),
        )
