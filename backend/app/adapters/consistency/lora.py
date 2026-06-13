"""LoRA 一致性:注入触发词,生成期挂载 LoRA(M4)。"""
from app.adapters.contracts import ConsistencyContext, ConsistencyStrategy, consistency_registry


@consistency_registry.register("lora")
class LoraConsistency(ConsistencyStrategy):
    name = "lora"

    def prompt_parts(self, ctx: ConsistencyContext) -> list[str]:
        trigger = (ctx.lora_ref or {}).get("trigger")
        return [trigger] if trigger else [ctx.asset_name]

    def generation_extras(self, ctx: ConsistencyContext) -> dict:
        return {"lora": ctx.lora_ref or {}}
