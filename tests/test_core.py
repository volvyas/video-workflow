import pytest

from shorts.comfy import template
from shorts.config import ConfigError, Env
from shorts.media.captions import ass_color, ass_time, group_words
from shorts.models import Script, WordTiming
from shorts.providers.align.faster_whisper import map_onto_script
from shorts.providers.base import LLMProvider
from shorts.providers.llm._schema import extract_json, strict_schema
from shorts.registry import create, resolve
from shorts.stages.visuals import wants_primary


def env(**kw) -> Env:
    return Env({k.upper(): str(v) for k, v in kw.items()})


# ---------------------------------------------------------------- config / registry
def test_app_settings_from_env():
    e = env(llm_provider="ollama", video_target_seconds="30", captions_enabled="false", music_dir="")
    assert e.app.llm_provider == "ollama"
    assert e.app.video_target_seconds == 30
    assert e.app.captions_enabled is False
    assert e.app.llm_for("script") == "ollama"


def test_stage_llm_override():
    e = env(llm_provider="claude", ideas_llm_provider="ollama")
    assert e.app.llm_for("ideas") == "ollama"
    assert e.app.llm_for("script") == "claude"


def test_provider_settings_by_prefix():
    p = create("llm", "ollama", env(llm_ollama_model="llama3.1:8b", llm_ollama_temperature="0.3"))
    assert p.settings.model == "llama3.1:8b"
    assert p.settings.temperature == 0.3


def test_provider_settings_typo_is_rejected():
    with pytest.raises(ConfigError):
        create("llm", "ollama", env(llm_ollama_modle="x"))


def test_unknown_provider():
    with pytest.raises(ConfigError, match="unknown llm provider"):
        resolve("llm", "nope")


def test_external_provider_by_import_path():
    cls = resolve("llm", "shorts.providers.llm.mock:MockLLM")
    assert issubclass(cls, LLMProvider)


def test_external_provider_wrong_kind():
    with pytest.raises(ConfigError, match="is not a"):
        resolve("tts", "shorts.providers.llm.mock:MockLLM")


# ---------------------------------------------------------------- llm helpers
def test_strict_schema_is_closed_and_inlined():
    s = strict_schema(Script)
    assert "$defs" not in s
    assert s["additionalProperties"] is False
    scene = s["properties"]["scenes"]["items"]
    assert scene["additionalProperties"] is False
    assert set(scene["required"]) == set(scene["properties"])
    assert "enum" in scene["properties"]["camera"]
    # fields literally named like schema metadata ("title") must survive
    assert "title" in s["properties"] and "title" in s["required"]
    assert "title" not in s["properties"]["title"]  # ...while metadata titles are dropped


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}


# ---------------------------------------------------------------- comfy templates
WF = {
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 20, "model": ["4", 0]}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}, masterpiece"}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
}


def test_template_fill_typed_and_inline():
    out = template.fill(WF, {"seed": 42, "prompt": "a cat"})
    assert out["3"]["inputs"]["seed"] == 42
    assert out["6"]["inputs"]["text"] == "a cat, masterpiece"
    assert WF["3"]["inputs"]["seed"] == "{{seed}}"  # original untouched


def test_template_missing_value():
    with pytest.raises(template.TemplateError):
        template.fill(WF, {"seed": 1})


def test_template_mark():
    wf = template.mark(WF, ["9.filename_prefix=filename_prefix", "3.steps=steps"])
    assert template.placeholders(wf) == {"seed", "prompt", "filename_prefix", "steps"}
    with pytest.raises(template.TemplateError):
        template.mark(WF, ["3.nope=x"])


# ---------------------------------------------------------------- captions / alignment / policy
def test_ass_helpers():
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_time(3725.456) == "1:02:05.46"


def test_group_words_breaks_on_punctuation():
    ws = [WordTiming(word=w, start=i, end=i + 1) for i, w in enumerate("one two, three four five six".split())]
    assert [len(g) for g in group_words(ws, 3)] == [2, 3, 1]


def test_map_onto_script_keeps_script_spelling():
    script = "In 1969 humans landed".split()
    heard = [("In", 0.0, 0.2), ("nineteen", 0.2, 0.5), ("sixty-nine", 0.5, 0.9), ("humans", 1.0, 1.3), ("landed", 1.3, 1.7)]
    out = map_onto_script(script, heard)
    assert [w.word for w in out] == script
    assert out[1].start == pytest.approx(0.2) and out[1].end == pytest.approx(0.9)
    assert out[3].start == pytest.approx(1.3)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(out, out[1:]))


@pytest.mark.parametrize("policy,expected", [
    ("all", [True] * 4), ("none", [False] * 4), ("first:1", [True, False, False, False]),
    ("every:2", [True, False, True, False]),
])
def test_motion_policy(policy, expected):
    assert [wants_primary(policy, i) for i in range(4)] == expected
