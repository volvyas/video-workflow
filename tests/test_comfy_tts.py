"""ComfyUI TTS provider against a simulated ComfyUI server (no network)."""

import json
import wave

import httpx
import pytest

from shorts.config import Env
from shorts.media import ffmpeg
from shorts.registry import create


@pytest.fixture
def fake_comfy(tmp_path):
    flac = tmp_path / "server_out.flac"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=200:duration=1.5", "-ac", "1", flac])
    calls = {"uploads": [], "prompts": []}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/upload/image":
            name = req.content.split(b'filename="')[1].split(b'"')[0].decode()
            calls["uploads"].append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if path == "/prompt":
            calls["prompts"].append(json.loads(req.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": f"p{len(calls['prompts'])}"})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"3": {"audio": [{"filename": "voice_00001_.flac", "subfolder": "shorts", "type": "output"}]}},
            }})
        if path == "/view":
            return httpx.Response(200, content=flac.read_bytes())
        if path == "/free":
            return httpx.Response(200)
        return httpx.Response(404)

    return handler, calls


def test_comfy_tts_end_to_end(tmp_path, fake_comfy, monkeypatch):
    handler, calls = fake_comfy
    ref = tmp_path / "ref.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=duration=1", ref])
    env = Env({"TTS_COMFY_REF_AUDIO": str(ref), "TTS_COMFY_REF_TEXT": "hello there",
               "TTS_COMFY_WORKFLOW": "workflows/tts_f5_comfy.json", "TTS_COMFY_SPEED": "1.1"})
    p = create("tts", "comfyui", env)
    p.verify()
    p._client._http = httpx.Client(base_url="http://comfy", transport=httpx.MockTransport(handler))
    p._client.poll_interval = 0

    out1, out2 = tmp_path / "scene_01.wav", tmp_path / "scene_02.wav"
    d1 = p.synthesize("First line.", out1)
    p.synthesize("Second line.", out2)

    assert d1 == pytest.approx(1.5, abs=0.05)
    with wave.open(str(out1)) as w:  # converted to PCM WAV
        assert w.getnchannels() == 1
    assert not (tmp_path / "scene_01.flac").exists()
    assert len(calls["uploads"]) == 1 and calls["uploads"][0].startswith("shorts_ref_")  # uploaded once
    wf = calls["prompts"][0]
    assert wf["1"]["inputs"]["audio"] == calls["uploads"][0]
    assert wf["2"]["inputs"]["speech"] == "First line."
    assert wf["2"]["inputs"]["sample_text"] == "hello there"
    assert wf["2"]["inputs"]["speed"] == 1.1 and wf["2"]["inputs"]["seed"] == 1
    assert calls["prompts"][1]["2"]["inputs"]["speech"] == "Second line."


def test_comfy_tts_requires_ref_text(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    p = create("tts", "comfyui", Env({"TTS_COMFY_REF_AUDIO": str(ref)}))
    with pytest.raises(Exception, match="REF_TEXT"):
        p.verify()
