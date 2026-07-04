from __future__ import annotations

from app.utils import sanitize_segment


def test_sanitize_segment_preserves_international_language_characters() -> None:
    assert sanitize_segment("イラスト") == "イラスト"
    assert sanitize_segment("中文素材/叔母ちゃん_ろまあぽ.txt") == "中文素材_叔母ちゃん_ろまあぽ.txt"
    assert sanitize_segment("mañana_café_مرحبا_नमस्ते.txt") == "mañana_café_مرحبا_नमस्ते.txt"
    assert sanitize_segment("作者 名:模型*V1?.safetensors") == "作者_名_模型_V1_.safetensors"
