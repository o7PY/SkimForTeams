import logging

import pytest
from pydantic import ValidationError

from skim.ai.config import AIConfig
from skim.ai.schemas import MessageExtraction, strict_json_schema

ENV = {"GROQ_API_KEY": "gk", "GEMINI_API_KEY": "mk", "GROQ_MODEL": "m"}


def test_enabled_with_defaults_and_overrides():
    c = AIConfig.from_env({**ENV, "AI_MAX_MESSAGES_PER_RUN": "5", "STAFF_AAD_IDS": "a, b ,"})
    assert c.enabled and c.max_messages_per_run == 5 and c.staff_aad_ids == {"a", "b"} and c.embed_dims == 768
    assert "gk" not in repr(c) and "mk" not in repr(c)


@pytest.mark.parametrize("env", [{**ENV, "AI_ENABLED": "false"}, {"GEMINI_API_KEY": "x", "GROQ_MODEL": "m"},
                                 {"GROQ_API_KEY": "x", "GROQ_MODEL": "m"}, {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y"}])
def test_switches_off_with_one_log_line_and_never_raises(env, caplog):
    with caplog.at_level(logging.INFO, logger="skim.ai"):
        c = AIConfig.from_env(env)
    assert not c.enabled and len(caplog.records) == 1
    assert "gk" not in caplog.text and "= x" not in caplog.text


def test_schema_rejects_bad_output():
    ok = {"category": "chitchat", "confidence": "high", "items": []}
    MessageExtraction.model_validate(ok)
    for bad in ({**ok, "category": "gossip"}, {**ok, "extra": 1}, {"category": "event"}):
        with pytest.raises(ValidationError):
            MessageExtraction.model_validate(bad)


def test_strict_schema_is_closed_and_complete():
    s = strict_json_schema(MessageExtraction)

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n["additionalProperties"] is False and set(n["required"]) == set(n["properties"])
            assert "$ref" not in n and "$defs" not in n
            [walk(v) for v in n.values()]
        elif isinstance(n, list):
            [walk(v) for v in n]
    walk(s)


def test_strict_schema_keeps_every_model_field():
    """Regression (first real eval): the schema cleaner dropped the field named 'title', so strict mode forbade it."""
    from skim.ai.schemas import Item, MessageExtraction, When, Relative, strict_json_schema

    schema = strict_json_schema(MessageExtraction)
    item = schema["properties"]["items"]["items"]
    when = item["properties"]["when"]
    rel = when["properties"]["relative"]
    for node, model in ((schema, MessageExtraction), (item, Item), (when, When), (rel, Relative)):
        assert set(node["properties"]) == set(model.model_fields)
        assert set(node["required"]) == set(model.model_fields)
    assert "title" in item["properties"]


def test_nim_is_optional_config_and_added_as_last_provider():
    from skim.ai.config import AIConfig
    from skim.ai.services import AIServices
    base = {"GROQ_API_KEY": "g", "GEMINI_API_KEY": "m", "GROQ_MODEL": "x"}
    assert not AIConfig.from_env(base).nim_api_key
    cfg = AIConfig.from_env({**base, "NIM_API_KEY": "nvapi-SECRET", "NIM_MODEL": "meta/llama"})
    assert cfg.enabled and "nvapi-SECRET" not in repr(cfg)
    assert [p.name for p in AIServices.build(cfg, object()).router.providers] == ["gemini", "groq", "nim"]
    assert [p.name for p in AIServices.build(AIConfig.from_env(base), object()).router.providers] == ["gemini", "groq"]
