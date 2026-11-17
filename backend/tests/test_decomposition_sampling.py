from app.adapters.decomposition.cloud_llm import _chat


def test_decomposition_preserves_model_sampling_default(monkeypatch):
    captured = {}
    def chat(*args, **kwargs):
        captured.update(kwargs)
        return ('{}', {})
    monkeypatch.setattr('app.adapters.model_sdk.chat', chat)
    assert _chat('system', 'chapter', 'https://example.test/v1', 'test', 'gpt-6-astra') == '{}'
    assert 'temperature' not in captured
    assert captured['provider'] == 'openai'
