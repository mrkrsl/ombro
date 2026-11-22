from __future__ import annotations

from ombro.thinking import openai_fields


def test_deepseek_matched_by_host_not_suffix():
    assert openai_fields("off", "https://api.deepseek.com/v1", "deepseek-chat") == {"thinking": {"type": "disabled"}}
    assert openai_fields("off", "https://evildeepseek.com/v1", "m") == {"reasoning_effort": "none"}
