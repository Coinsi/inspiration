"""Mock 助手引擎:纯规则启发式,无需外部 LLM。

目的:离线打通"对话→修改提议→应用"全链路、用于自动化验收。
真实创作质量由 cloud_llm 引擎提供。规则覆盖最常见的几类指令:
- 改标题   → set_title
- 加台词/对白 → insert_block(dialogue)
- 删除最后一块 → delete_block
- 其它      → 给出回复并把指令作为一句动作行追加(insert_block action),保证 ops 可被应用
"""
import re

from app.adapters.contracts import AssistEngine, AssistProposal, assist_registry


@assist_registry.register("mock")
class MockAssist(AssistEngine):
    name = "mock"

    def propose(self, *, object_desc, ops_help, state, history, instruction, **_) -> AssistProposal:
        text = (instruction or "").strip()
        low = text.lower()
        blocks = state.get("blocks") or []

        def _payload(keywords) -> str:
            """取冒号后的内容;无冒号则去掉前导关键词短语。"""
            if "：" in text or ":" in text:
                return re.split(r"[:：]", text, 1)[1].strip()
            pat = r"^.*?(?:" + "|".join(keywords) + r")\s*(?:改成|设为|为|成|to)?\s*"
            return re.sub(pat, "", text, flags=re.I).strip()

        # 1) 改标题
        if "标题" in text or "title" in low:
            new_title = _payload(["标题", "title"]).strip("。.\"“”'")
            if new_title:
                return AssistProposal(
                    reply=f"已将标题改为「{new_title}」。",
                    summary=f"标题 → {new_title}",
                    ops=[{"op": "set_title", "text": new_title}],
                )

        # 2) 加台词/对白(剧本对象)
        if any(k in text for k in ("台词", "对白", "说一句", "加一句")) and "blocks" in state:
            line = _payload(["台词", "对白", "说一句", "加一句"]) or "……"
            return AssistProposal(
                reply=f"已追加一句对白:{line}",
                summary="新增对白",
                ops=[{"op": "insert_block", "block_type": "dialogue", "text": line}],
            )

        # 3) 删除最后一块
        if ("删除" in text or "删掉" in text) and blocks:
            last_id = blocks[-1].get("id")
            return AssistProposal(
                reply="已删除最后一个块。",
                summary="删除末块",
                ops=[{"op": "delete_block", "id": last_id}],
            )

        # 4) 通用字段对象(scene/shot/asset/prompt):把指令写入第一个可编辑字段
        fields = state.get("fields")
        if isinstance(fields, dict) and fields:
            field = next(iter(fields))
            return AssistProposal(
                reply=f"已更新字段「{field}」。",
                summary=f"set {field}",
                ops=[{"op": "set_field", "field": field, "value": text}],
            )

        # 5) 兜底:剧本追加一句动作行
        if "blocks" in state:
            return AssistProposal(
                reply=f"已把你的要求作为动作行追加:{text}",
                summary="追加动作行",
                ops=[{"op": "insert_block", "block_type": "action", "text": text or "(空)"}],
            )

        return AssistProposal(reply="收到,但当前对象没有可修改的内容。", needs_clarification=True)
