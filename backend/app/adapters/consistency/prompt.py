"""纯提示词一致性:注入角色/场景的专属提示词片段。"""
from app.adapters.contracts import ConsistencyContext, ConsistencyStrategy, consistency_registry


@consistency_registry.register("prompt")
class PromptConsistency(ConsistencyStrategy):
    name = "prompt"

    def prompt_parts(self, ctx: ConsistencyContext) -> list[str]:
        if ctx.prompt_fragment_text:
            return [ctx.prompt_fragment_text]
        return [ctx.asset_name]
