"""Ollama planner constrained to the A4 CP²N² tool boundary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.constrained_client import (  # noqa: E402
    AgentResult,
    ConstrainedAgentExecutor,
    PLANNING_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    build_agent_surface,
)
from mcp_surface.service import MCPControlSurface  # noqa: E402


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 180.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or "qwen2.5:7b-instruct"
        self.timeout_s = timeout_s

    def generate(self, prompt: str, *, temperature: float = 0.1) -> str:
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("response")
        if not isinstance(text, str):
            raise RuntimeError(f"Unexpected Ollama response: {payload}")
        return text.strip()

    def healthcheck(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/api/tags", timeout=30.0)
        response.raise_for_status()
        return response.json()


class CP2N2OllamaAgent:
    def __init__(
        self,
        model: str | None = None,
        ollama_base_url: str | None = None,
        *,
        surface: MCPControlSurface | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.llm = OllamaClient(base_url=ollama_base_url, model=model)
        self.surface = surface or build_agent_surface(
            principal_id="ollama-agent",
            audit_path=audit_path
            or Path(".cp2n2") / "ollama-agent-audit.jsonl",
            include_cortical_labs=True,
        )
        self.executor = ConstrainedAgentExecutor(self.surface)

    def discover_resources(self) -> list[dict[str, Any]]:
        return self.executor.discover_resources()

    def plan(self, user_goal: str) -> dict[str, Any]:
        resources = self.discover_resources()
        prompt = (
            f"{PLANNING_PROMPT}\n\n"
            f"Discovered resources: {json.dumps(resources)}\n\n"
            f"User goal: {user_goal}\n"
        )
        return json.loads(self.llm.generate(prompt, temperature=0.1))

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
        return self.llm.generate(prompt, temperature=0.2)

    def run(
        self,
        user_goal: str,
        *,
        approval_token: str | None = None,
    ) -> AgentResult:
        resources = self.discover_resources()
        plan = self.plan(user_goal)
        run_result = self.execute_plan(plan, approval_token=approval_token)
        summary = self.summarize(user_goal, plan, run_result)
        return AgentResult(
            plan=plan,
            resources=resources,
            run_result=run_result,
            summary=summary,
        )


# Backward-compatible import for pre-rename clients.
PhysMCPOllamaAgent = CP2N2OllamaAgent


def main() -> None:
    agent = CP2N2OllamaAgent()
    print(json.dumps(agent.llm.healthcheck(), indent=2)[:3000])
    result = agent.run(
        "Prepare a dry-run plan for a compatible server-owned assay preset."
    )
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
