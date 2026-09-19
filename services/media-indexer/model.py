"""Embedding checkpoint wrapper: weights use the `model.` namespace, without an LM head.

Architecture reference: QwenLM/Qwen3-VL-Embedding/src/models/qwen3_vl_embedding.py.
Keeping this namespace is essential; loading the bare AutoModel can initialize random weights.
"""

from typing import ClassVar

from transformers.models.qwen3_vl.modeling_qwen3_vl import (
    Qwen3VLModel,
    Qwen3VLPreTrainedModel,
)


class EmbeddingModel(Qwen3VLPreTrainedModel):
    _checkpoint_conversion_mapping: ClassVar[dict] = {}
    accepts_loss_kwargs = False

    def __init__(self, config):
        super().__init__(config)
        self.model = Qwen3VLModel(config)
        self.post_init()

    def get_input_embeddings(self):
        return self.model.get_input_embeddings()

    def forward(self, **kwargs):
        return self.model(**kwargs)
