from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from pydantic import ValidationError

from agent.ai_lab_agent import (
    AI_LAB_BASE_URL,
    AILabCompletion,
    AILabConfiguration,
    AILabAPIError,
    AILabPlanFormatError,
    OpenAICompatibleAILabClient,
    CP2N2AILabAgent,
    extract_agent_plan,
    strip_reasoning_wrapper,
)
from agent.constrained_client import build_agent_surface


VALID_PLAN = {
    "action": "prepare_assay",
    "arguments": {
        "resource_id": "edge-backend",
        "preset_id": "edge_vector_classification_v1",
        "dry_run": True,
    },
    "rationale": "Assess the fixed edge preset without execution.",
}


class FakeAILabLLM:
    model = "mock-ai-lab-model"

    def __init__(self, plan: dict | None = None) -> None:
        self.plan_payload = plan or VALID_PLAN
        self.calls: list[dict] = []

    def complete(self, messages, *, temperature, json_mode=False):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "json_mode": json_mode,
            }
        )
        content = (
            json.dumps(self.plan_payload)
            if json_mode
            else "Dry-run planning completed through the constrained MCP surface."
        )
        return AILabCompletion(
            content=content,
            requested_model=self.model,
            response_model=self.model,
            request_id=f"mock-{len(self.calls)}",
            usage={"total_tokens": 10},
        )


def _agent(tmp_path: Path, llm: FakeAILabLLM) -> CP2N2AILabAgent:
    surface = build_agent_surface(
        principal_id="ai-lab-test-agent",
        audit_path=tmp_path / "audit.jsonl",
        include_cortical_labs=False,
    )
    return CP2N2AILabAgent(llm=llm, surface=surface)


def test_ai_lab_dry_run_is_audited_and_has_no_resource_commitment(
    tmp_path: Path,
) -> None:
    llm = FakeAILabLLM()
    agent = _agent(tmp_path, llm)
    adapter = agent.surface.orchestrator.registry.get_adapter("edge-backend")

    def forbidden_invoke(_task):
        raise AssertionError("dry-run agent reached the substrate runtime")

    adapter.invoke = forbidden_invoke  # type: ignore[method-assign]
    result = agent.run("Assess the edge preset without substrate execution.")

    assert result.run_result["success"] is True
    assert result.run_result["dry_run"] is True
    assert result.run_result["raw_substrate_output_exposed"] is False
    assert (
        agent.surface.orchestrator.registry.lease_store.current("edge-backend")
        is None
    )
    assert agent.surface.audit_trail.verify()
    assert len(agent.surface.audit_trail.events()) == 6
    assert len(llm.calls) == 2
    assert llm.calls[0]["json_mode"] is True


def test_ai_lab_hostile_plan_is_rejected_before_mcp_execution(
    tmp_path: Path,
) -> None:
    hostile = {
        "action": "prepare_assay",
        "arguments": {
            **VALID_PLAN["arguments"],
            "amplitude": 999,
            "runtime_kind": "physical_hardware",
            "approval_token": "model-minted",
        },
        "rationale": "Attempt authority expansion.",
    }
    agent = _agent(tmp_path, FakeAILabLLM(hostile))

    with pytest.raises(AILabPlanFormatError):
        agent.plan("Try unsafe controls.")

    assert agent.surface.orchestrator.registry.lease_store.current(
        "edge-backend"
    ) is None
    events = agent.surface.audit_trail.events()
    assert [event.tool for event in events] == [
        "discover_resources",
        "discover_resources",
    ]


def test_provider_reasoning_wrapper_yields_one_strict_plan() -> None:
    wrapped = (
        "<think>I should choose a fixed dry-run preset.</think>\n"
        + json.dumps(VALID_PLAN)
    )
    assert extract_agent_plan(wrapped).model_dump(mode="json") == VALID_PLAN


def test_ambiguous_multiple_valid_plans_are_rejected() -> None:
    content = json.dumps(VALID_PLAN) + "\n" + json.dumps(VALID_PLAN)
    with pytest.raises(AILabPlanFormatError, match="exactly one"):
        extract_agent_plan(content)


def test_provider_reasoning_wrapper_is_removed_from_summary() -> None:
    wrapped = "<think>Private reasoning.</think>\n\nVisible summary."
    assert strip_reasoning_wrapper(wrapped) == "Visible summary."
    assert strip_reasoning_wrapper("Already visible.") == "Already visible."


def test_ai_lab_configuration_pins_host_and_redacts_secret() -> None:
    configuration = AILabConfiguration(api_key="sk-super-secret")
    assert configuration.base_url == AI_LAB_BASE_URL
    assert "sk-super-secret" not in repr(configuration)

    with pytest.raises(ValidationError, match="official HTTPS host"):
        AILabConfiguration(
            api_key="sk-super-secret",
            base_url="https://attacker.example/v1",
        )


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_openai_compatible_client_uses_documented_endpoints_and_provenance() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "chatcmpl-1",
                    "model": "minimax-m2.7",
                    "choices": [{"message": {"content": "{\"ok\": true}"}}],
                    "usage": {"total_tokens": 42},
                }
            ),
            FakeResponse({"data": [{"id": "qwen3.6-27b"}, {"id": "minimax-m2.7"}]}),
            FakeResponse({"user_info": {"spend": 1.5, "max_budget": 50.0}}),
        ]
    )
    configuration = AILabConfiguration(api_key="sk-test")
    client = OpenAICompatibleAILabClient(
        configuration,
        session=session,  # type: ignore[arg-type]
    )

    completion = client.complete(
        [{"role": "user", "content": "Return JSON."}],
        temperature=0.1,
        json_mode=True,
    )
    assert completion.response_model == "minimax-m2.7"
    assert completion.request_id == "chatcmpl-1"
    assert completion.usage["total_tokens"] == 42
    assert client.list_models() == ["minimax-m2.7", "qwen3.6-27b"]
    assert client.budget_info() == {
        "spend": 1.5,
        "max_budget": 50.0,
        "reset_schedule": "weekly",
    }
    assert session.calls[0]["url"] == AI_LAB_BASE_URL + "/v1/chat/completions"
    assert session.calls[1]["url"] == AI_LAB_BASE_URL + "/v1/models"
    assert session.calls[2]["url"] == AI_LAB_BASE_URL + "/user/info"
    assert session.calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_provider_errors_do_not_expose_api_key_or_response_body() -> None:
    secret = "sk-never-log-this"
    session = FakeSession([FakeResponse({"detail": secret}, status_code=401)])
    client = OpenAICompatibleAILabClient(
        AILabConfiguration(api_key=secret),
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(AILabAPIError) as captured:
        client.list_models()

    assert secret not in str(captured.value)
    assert secret not in repr(client.configuration)


def test_ai_lab_agent_module_has_no_direct_backend_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "agent" / "ai_lab_agent.py"
    ).read_text(encoding="utf-8")
    assert "from adapters" not in source
    assert "import adapters" not in source
    assert "from backends" not in source
    assert "import backends" not in source
