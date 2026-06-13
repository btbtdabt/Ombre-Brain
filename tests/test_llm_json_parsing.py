import pytest

from dehydrator import ANALYZE_PROMPT, DIGEST_PROMPT_TEMPLATE, Dehydrator
from reflection_engine import ReflectionEngine
from utils import parse_first_json_value


def test_parse_first_json_value_accepts_fenced_object_with_tail():
    parsed = parse_first_json_value('```json\n{"domain":["饮食"],"valence":0.7}\n```\n说明')

    assert parsed == {"domain": ["饮食"], "valence": 0.7}


def test_parse_first_json_value_accepts_embedded_array():
    parsed = parse_first_json_value('结果如下：\n[{"name":"海鲜","content":"艾米喜欢海鲜。"}]\n完成')

    assert parsed == [{"name": "海鲜", "content": "艾米喜欢海鲜。"}]


def test_parse_first_json_value_rejects_non_json_field_list():
    with pytest.raises(ValueError):
        parse_first_json_value("`memory_subject`: user / relationship")


def test_dehydrator_parse_analysis_uses_first_json_value(test_config):
    dehydrator = Dehydrator(test_config)

    parsed = dehydrator._parse_analysis(
        '```json\n'
        '{"domain":["饮食"],"valence":0.8,"arousal":0.4,'
        '"tags":["海鲜"],"suggested_name":"海鲜偏好",'
        '"memory_subject":"user","memory_layer":"stable_boundary"}'
        '\n```\n说明'
    )

    assert parsed["domain"] == ["饮食"]
    assert parsed["valence"] == 0.8
    assert parsed["tags"] == ["海鲜"]
    assert parsed["suggested_name"] == "海鲜偏好"
    assert parsed["memory_subject"] == "user"
    assert parsed["memory_layer"] == "stable_boundary"


def test_dehydrator_parse_digest_uses_first_json_value(test_config):
    dehydrator = Dehydrator(test_config)

    parsed = dehydrator._parse_digest(
        '结果如下：\n'
        '[{"name":"海鲜偏好","content":"### moment\\n艾米喜欢吃海鲜。",'
        '"domain":["饮食"],"valence":0.8,"arousal":0.4,'
        '"tags":["海鲜"],"importance":6,'
        '"memory_subject":"user","memory_layer":"stable_boundary"}]'
        '\n完成'
    )

    assert len(parsed) == 1
    assert parsed[0]["name"] == "海鲜偏好"
    assert parsed[0]["domain"] == ["饮食"]
    assert parsed[0]["memory_subject"] == "user"


def test_reflection_parse_json_object_uses_first_json_value(test_config):
    engine = ReflectionEngine(test_config)

    parsed = engine._parse_json_object('```json\n{"tags":["relationship_event"],"importance":6}\n```\n说明')

    assert parsed == {"tags": ["relationship_event"], "importance": 6}


def test_dehydrator_prompts_have_strict_json_contract():
    assert "输出格式（必须按照此格式输出）" in ANALYZE_PROMPT
    assert "输出必须是一个合法 JSON object。" in ANALYZE_PROMPT
    assert "输出格式（必须按照此格式输出）" in DIGEST_PROMPT_TEMPLATE
    assert "输出必须是一个合法 JSON array。" in DIGEST_PROMPT_TEMPLATE
