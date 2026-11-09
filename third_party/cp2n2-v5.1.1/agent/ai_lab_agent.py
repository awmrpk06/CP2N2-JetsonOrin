"""University of Lübeck AI-Lab planner constrained to the A4 MCP boundary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()
load_dotenv()

from agent.constrained_client import (  # noqa: E402
    AgentPlan,
    AgentResult,
    ConstrainedAgentExecutor,
    PLANNING_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    build_agent_surface,
)
from mcp_surface.service import MCPControlSurface  # noqa: E402


AI_LAB_BASE_URL = "https://llm-api.ai-lab.uni-luebeck.de"
AI_LAB_DEFAULT_MODEL = "minimax-m2.7"
AI_LAB_HOST = "llm-api.ai-lab.uni-luebeck.de"


class AILabConfiguration(BaseModel):
    """Credential-safe, host-pinned AI-Lab client configuration."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    base_url: str = AI_LAB_BASE_URL
    model: str = AI_LAB_DEFAULT_MODEL
    timeout_s: float = Field(default=180.0, gt=0.0, le=600.0)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("AI-Lab API key must not be empty")
        return SecretStr(secret)

    @field_validator("base_url")
    @classmethod
    def pin_ai_lab_host(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or parsed.hostname != AI_LAB_HOST:
            raise ValueError(
                "AI-Lab credentials may only be sent to the official HTTPS host"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("AI-Lab base_url must not contain a path or query")
        return normalized

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("AI-Lab model must be a non-empty model identifier")
        return normalized

    @classmethod
    def from_environment(cls) -> "AILabConfiguration":
        api_key = os.getenv("AI_LAB_API_KEY") or os.getenv("LITELLM_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Missing AI_LAB_API_KEY (or LITELLM_API_KEY) in the environment."
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("AI_LAB_BASE_URL", AI_LAB_BASE_URL),
            model=os.getenv("AI_LAB_MODEL", AI_LAB_DEFAULT_MODEL),
        )


class AILabCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    requested_model: str
    response_model: str | None = None
    request_id: str | None = None
    usage: dict[str, int | float | None] = Field(default_factory=dict)


class AILabLLM(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        json_mode: bool = False,
    ) -> AILabCompletion: ...


class AILabAPIError(RuntimeError):
    """Sanitized provider failure that never includes credentials or bodies."""


class AILabPlanFormatError(ValueError):
    """Sanitized failure for ambiguous or schema-invalid model output."""


def extract_agent_plan(content: str) -> AgentPlan:
    """Extract exactly one schema-valid plan from optional provider wrappers."""

    decoder = json.JSONDecoder()
    valid_plans: list[AgentPlan] = []
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            valid_plans.append(AgentPlan.model_validate(payload))
        except Exception:
            continue
    if len(valid_plans) != 1:
        raise AILabPlanFormatError(
            "AI-Lab response did not contain exactly one valid AgentPlan"
        )
    return valid_plans[0]


def strip_reasoning_wrapper(content: str) -> str:
    """Remove a leading provider reasoning block from a user-facing summary."""

    cleaned = content.strip()
    if cleaned.startswith("<think>"):
        closing_tag = cleaned.find("</think>")
        if closing_tag >= 0:
            cleaned = cleaned[closing_tag + len("</think>") :].lstrip()
    return cleaned


class OpenAICompatibleAILabClient:
    """Minimal LiteLLM client for the official AI-Lab host."""

    def __init__(
        self,
        configuration: AILabConfiguration,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.configuration = configuration
        self.model = configuration.model
        self._session = session or requests.Session()

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        json_mode: bool = False,
    ) -> AILabCompletion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self._request("POST", "/v1/chat/completions", json=payload)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AILabAPIError("AI-Lab returned no completion choice")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AILabAPIError("AI-Lab returned an empty completion")
        usage = response.get("usage")
        return AILabCompletion(
            content=content.strip(),
            requested_model=self.model,
            response_model=(
                response.get("model")
                if isinstance(response.get("model"), str)
                else None
            ),
            request_id=(
                response.get("id")
                if isinstance(response.get("id"), str)
                else None
            ),
            usage=usage if isinstance(usage, dict) else {},
        )

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/v1/models")
        items = payload.get("data")
        if not isinstance(items, list):
            raise AILabAPIError("AI-Lab returned an invalid model catalog")
        return sorted(
            {
                item["id"]
                for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        )

    def budget_info(self) -> dict[str, float | int | str | None]:
        payload = self._request("GET", "/user/info")
        user_info = payload.get("user_info")
        if not isinstance(user_info, dict):
            raise AILabAPIError("AI-Lab returned invalid budget information")
        return {
            "spend": user_info.get("spend"),
            "max_budget": user_info.get("max_budget"),
            "reset_schedule": "weekly",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self.configuration.base_url + path
        try:
            response = self._session.request(
                method,
                url,
                headers={
                    "Authorization": (
                        "Bearer "
                        + self.configuration.api_key.get_secret_value()
                    ),
                    "Content-Type": "application/json",
                },
                timeout=self.configuration.timeout_s,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status is not None else ""
            raise AILabAPIError(f"AI-Lab request failed{suffix}") from exc
        except ValueError as exc:
            raise AILabAPIError("AI-Lab returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AILabAPIError("AI-Lab returned a non-object response")
        return payload


class CP2N2AILabAgent:
    """AI-Lab agent with exactly the same authority as the A4 examples."""

    def __init__(
        self,
        *,
        llm: AILabLLM | None = None,
        surface: MCPControlSurface | None = None,
        audit_path: Path | None = None,
        include_cortical_labs: bool = True,
    ) -> None:
        self.llm = llm or OpenAICompatibleAILabClient(
            AILabConfiguration.from_environment()
        )
        self.surface = surface or build_agent_surface(
            principal_id="ai-lab-agent",
            audit_path=audit_path
            or Path(".cp2n2") / "ai-lab-agent-audit.jsonl",
            include_cortical_labs=include_cortical_labs,
        )
        self.executor = ConstrainedAgentExecutor(self.surface)
        self.last_plan_completion: AILabCompletion | None = None
        self.last_summary_completion: AILabCompletion | None = None

    def discover_resources(self) -> list[dict[str, Any]]:
        return self.executor.discover_resources()

    def plan(
        self,
        user_goal: str,
        *,
        resources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resources = resources if resources is not None else self.discover_resources()
        completion = self.llm.complete(
            [
                {"role": "system", "content": PLANNING_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Discovered resources: {json.dumps(resources)}\n\n"
                        f"User goal: {user_goal}"
                    ),
                },
            ],
            temperature=0.1,
            json_mode=True,
        )
        self.last_plan_completion = completion
        plan = extract_agent_plan(completion.content)
        return plan.model_dump(mode="json")

    def execute_plan(
        self,
        plan: dict[str, Any],
        *,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        return self.executor.execute_plan(
            plan,
            approval_token=approval_token,
        )

    def summarize(
        self,
        user_goal: str,
        plan: dict[str, Any],
        run_result: dict[str, Any],
    ) -> str:
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            plan_json=json.dumps(plan, indent=2),
            result_json=json.dumps(run_result, indent=2),
        )
        completion = self.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        self.last_summary_completion = completion
        return strip_reasoning_wrapper(completion.content)

    def run(
        self,
        user_goal: str,
        *,
        approval_token: str | None = None,
    ) -> AgentResult:
        resources = self.discover_resources()
        plan = self.plan(user_goal, resources=resources)
        run_result = self.execute_plan(plan, approval_token=approval_token)
        summary = self.summarize(user_goal, plan, run_result)
        return AgentResult(
            plan=plan,
            resources=resources,
            run_result=run_result,
            summary=summary,
        )


# Backward-compatible import for pre-rename clients.
PhysMCPAILabAgent = CP2N2AILabAgent


def main() -> None:
    result = CP2N2AILabAgent().run(
        "Prepare a dry-run plan for a compatible server-owned assay preset."
    )
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
