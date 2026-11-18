from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import json
import threading

import app as claudex


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(claudex, "DATA", data_dir)
    monkeypatch.setattr(claudex, "DB_FILE", data_dir / "claudex.sqlite3")
    monkeypatch.setattr(claudex, "ENV_FILE", env_file)
    for key in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_REASONING_EFFORT", "AUTO_THINK", "AUTO_MEMORY", "COUNCIL_REVIEWER_LOGIC_MODEL", "COUNCIL_REVIEWER_SYSTEMS_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(claudex.app) as test_client:
        yield test_client


def test_config_separates_model_names_descriptions_and_effort_options(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    config = response.json()
    model = config["models"][claudex.DEFAULT_MODEL]
    assert model["name"] == "Nemotron 3.5 Lightning"
    assert "Fast responses" in model["description"]
    assert model["efforts"] == ["Low", "Medium", "High", "Extra", "Max"]
    assert config["effort"] == "Medium"


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("nvidia/nemotron-3.5-lightning-30b-a3b", "Low", {"extra_body": {"reasoning_budget": 2048}}),
        ("nvidia/nemotron-3.5-lightning-30b-a3b", "Max", {"extra_body": {"reasoning_budget": 32768}}),
        ("nvidia/nemotron-3-ultra-550b-a55b", "Off", {"reasoning_effort": "none"}),
        ("nvidia/nemotron-3-ultra-550b-a55b", "Medium", {"reasoning_effort": "medium"}),
        ("nvidia/nemotron-3.5-super-vl-120b-a12b", "Low", {"reasoning_effort": "low"}),
        (
            "nvidia/nemotron-3.5-bolt-e4b",
            "On",
            {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
        ),
        (
            "nvidia/nemotron-3.5-bolt-e4b",
            "Off",
            {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    ],
)
def test_effort_maps_to_documented_nvidia_parameters(model, effort, expected):
    assert claudex.reasoning_parameters(model, effort) == expected


def test_settings_persist_valid_model_effort_and_reject_unsupported_effort(client):
    model = "nvidia/nemotron-3-ultra-550b-a55b"
    valid = client.post("/api/settings", json={"model": model, "effort": "High"})
    assert valid.status_code == 200
    assert valid.json()["effort"] == "High"
    assert client.get("/api/config").json()["effort"] == "High"

    invalid = client.post("/api/settings", json={"model": model, "effort": "Extra"})
    assert invalid.status_code == 400
    assert "not supported" in invalid.json()["detail"]


def test_selected_effort_reaches_both_tool_and_final_inference_calls(client, monkeypatch):
    model = "nvidia/nemotron-3.5-lightning-30b-a3b"
    saved = client.post("/api/settings", json={"model": model, "effort": "Extra"})
    assert saved.status_code == 200
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-which-is-not-real")
    conversation = client.post("/api/conversations").json()["id"]
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="Grounded test response.")
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])
        message = SimpleNamespace(tool_calls=None, content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(claudex, "nvidia_client", lambda: fake_client)
    response = client.post(
        "/api/chat",
        json={"conversation_id": conversation, "message": "Explain the test.", "auto_think": False, "auto_memory": False},
    )
    assert response.status_code == 200
    assert "Grounded test response." in response.text
    assert len(calls) == 2
    assert all(call["extra_body"]["reasoning_budget"] == 24576 for call in calls)
    assert calls[-1]["stream"] is True


def test_composer_has_live_model_effort_menus_and_typed_send_state():
    html = (claudex.STATIC / "index.html").read_text(encoding="utf-8")
    script = (claudex.STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="modelPicker"' in html
    assert 'id="effortPicker"' in html
    assert 'id="modelEffortLabel"' in html
    assert '<svg viewBox="0 0 20 20"' in html
    assert '<svg viewBox="0 0 24 24"' in html
    assert "setSelectedEffort" in script
    assert "updateSendState" in script
    assert "model-option-description" in script


def test_plus_menu_is_upload_only_and_files_are_attached_to_model_context(client, monkeypatch):
    html = (claudex.STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="uploadFiles"' in html
    assert html.count('id="uploadFiles"') == 1
    assert 'id="fileInput" type="file" hidden multiple' in html

    conversation = client.post("/api/conversations").json()["id"]
    uploaded = client.post(
        f"/api/conversations/{conversation}/attachments",
        files=[("files", ("notes.md", b"# Project\n\nThe launch date is 12 June.", "text/markdown"))],
    )
    assert uploaded.status_code == 200
    attachment = uploaded.json()["attachments"][0]
    assert attachment["filename"] == "notes.md"

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-which-is-not-real")
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="The file says 12 June.")
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])
        message = SimpleNamespace(tool_calls=None, content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(claudex, "nvidia_client", lambda: fake_client)
    response = client.post(
        "/api/chat",
        json={"conversation_id": conversation, "message": "When is launch?", "attachment_ids": [attachment["id"]], "auto_think": False, "auto_memory": False},
    )
    assert response.status_code == 200
    assert "The file says 12 June." in response.text
    user_turn = next(message for message in captured[0]["messages"] if message["role"] == "user")
    assert "[Attached file: notes.md]" in user_turn["content"]
    assert "The launch date is 12 June." in user_turn["content"]


def test_upload_rejects_unsupported_files(client):
    conversation = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{conversation}/attachments",
        files=[("files", ("archive.exe", b"not a supported document", "application/octet-stream"))],
    )
    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_pdf_tool_creates_downloadable_formatted_pdf(client):
    from pypdf import PdfReader
    from io import BytesIO

    conversation = client.post("/api/conversations").json()["id"]
    _, summary = claudex.run_tool(
        "create_pdf",
        {"title": "Launch Brief", "content": "## Key dates\n\n- **Launch:** 12 June\n- Review $x^2$ before release."},
        conversation_id=conversation,
    )
    response = client.get(summary["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    reader = PdfReader(BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Launch Brief" in text
    assert "Key dates" in text
    assert "Launch:" in text
    assert "12 June" in text
    assert reader.pages[0]["/Resources"].get("/XObject")


def _council_chat(client, monkeypatch, reviewer_fn):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-which-is-not-real")
    conversation = client.post("/api/conversations").json()["id"]
    lead_calls = []

    def create(**kwargs):
        lead_calls.append(kwargs)
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="Synthesized test answer.")
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=None))])

    monkeypatch.setattr(claudex, "nvidia_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(claudex, "run_council_reviewer", reviewer_fn)
    response = client.post("/api/chat", json={
        "conversation_id": conversation, "message": "Design a reliable file indexer.",
        "auto_think": True, "auto_memory": False, "council_mode": True,
    })
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    first_lead = next(call for call in lead_calls if not call.get("stream"))
    return response, events, first_lead["messages"]


def test_council_runs_both_reviewers_in_parallel_and_synthesizes_consensus(client, monkeypatch):
    barrier = threading.Barrier(2, timeout=3)

    def review(model, role, context):
        barrier.wait()
        return json.dumps({
            "proposed_approach": "Index files by normalized path.",
            "key_assumptions": ["The index is local."],
            "blind_spots": ["Symlinks need a policy."],
            "checks": ["Test duplicate paths."],
        })

    response, events, messages = _council_chat(client, monkeypatch, review)
    assert response.status_code == 200
    assert "Synthesized test answer." in response.text
    assert len([event for event in events if event["type"] == "council_reviewer" and event["status"] == "complete"]) == 2
    system = messages[0]["content"]
    assert "COUNCIL REVIEW BRIEFS" in system
    assert "Index files by normalized path." in system
    assert "not evidence" in system.lower()


def test_council_passes_disagreement_to_lead_for_resolution(client, monkeypatch):
    def review(model, role, context):
        approach = "Use SQLite transactions." if role == "logic" else "Use append-only JSONL."
        return json.dumps({"proposed_approach": approach, "key_assumptions": [], "blind_spots": [], "checks": []})

    response, events, messages = _council_chat(client, monkeypatch, review)
    assert response.status_code == 200
    assert "Use SQLite transactions." in messages[0]["content"]
    assert "Use append-only JSONL." in messages[0]["content"]
    assert "disagreements" in messages[0]["content"].lower()
    assert next(event for event in events if event["type"] == "council_complete")["partial"] is False


def test_council_degrades_cleanly_when_reviewer_endpoint_is_unavailable(client, monkeypatch):
    class EndpointUnavailable(Exception):
        status_code = 403

    def review(model, role, context):
        if role == "logic":
            raise EndpointUnavailable("not enabled for test key")
        return json.dumps({"proposed_approach": "Proceed with bounded batches.", "key_assumptions": [], "blind_spots": [], "checks": []})

    response, events, messages = _council_chat(client, monkeypatch, review)
    assert response.status_code == 200
    failed = next(event for event in events if event["type"] == "council_reviewer" and event["status"] == "unavailable")
    assert "HTTP 403" in failed["summary"]
    complete = next(event for event in events if event["type"] == "council_complete")
    assert complete["partial"] is True and complete["completed"] == 1
    assert "PARTIAL COUNCIL" in messages[0]["content"]
    assert "Proceed with bounded batches." in messages[0]["content"]


def test_composer_exposes_council_mode_without_replacing_effort_picker():
    html = (claudex.STATIC / "index.html").read_text(encoding="utf-8")
    script = (claudex.STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="councilMode"' in html
    assert 'council_mode:councilMode' in script
    assert 'id="effortPicker"' in html
    assert "COUNCIL REVIEW BRIEFS" not in script


def test_council_reviewer_models_are_configurable_and_timeout_is_reported(client, monkeypatch):
    monkeypatch.setenv("COUNCIL_REVIEWER_LOGIC_MODEL", "qwen/qwen3-next-80b-a3b-thinking")
    monkeypatch.setenv("COUNCIL_REVIEWER_SYSTEMS_MODEL", "qwen/qwen3.5-122b-a10b")
    config = client.get("/api/config").json()
    assert [reviewer["model"] for reviewer in config["council_reviewers"]] == [
        "qwen/qwen3-next-80b-a3b-thinking", "qwen/qwen3.5-122b-a10b",
    ]
    status, explanation = claudex._council_error_status(TimeoutError("request timed out"))
    assert status == "timeout"
    assert "timed out" in explanation


def test_lightning_reasoning_budget_uses_supported_sdk_extra_body():
    import httpx
    from openai import OpenAI

    captured = {}

    def handle(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })

    transport = httpx.MockTransport(handle)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAI(api_key="nvapi-test-key", base_url=claudex.NVIDIA_BASE_URL, http_client=http_client)
        client.chat.completions.create(
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            messages=[{"role": "user", "content": "test"}],
            **claudex.reasoning_parameters("nvidia/nemotron-3.5-lightning-30b-a3b", "High"),
        )
    assert captured["reasoning_budget"] == 16384
