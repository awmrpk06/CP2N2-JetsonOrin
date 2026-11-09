"""Gemini planner constrained to the A4 CP²N² tool boundary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from google.genai import Client  # noqa: E402
from google.genai.types import GenerateContentConfig  # noqa: E402

from agent.constrained_client import (  # noqa: E402
    AgentResult,
    ConstrainedAgentExecutor,
    PLANNING_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    build_agent_surface,
)
from mcp_surface.service import MCPControlSurface  # noqa: E402


class CP2N2GeminiAgent:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-pro",
        *,
        surface: MCPControlSurface | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("Missing GEMINI_API_KEY in environment.")
        self.client = Client(api_key=self.api_key)
        self.model = model
        self.surface = surface or build_agent_surface(
            principal_id="gemini-agent",
            audit_path=audit_path
            or Path(".cp2n2") / "gemini-agent-audit.jsonl",
            include_cortical_labs=True,
        )
        self.executor = ConstrainedAgentExecutor(self.surface)

    def discover_resources(self) -> list[dict[str, Any]]:
        return self.executor.discover_resources()

    def plan(self, user_goal: str) -> dict[str, Any]:
        resources = self.discover_resources()
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                PLANNING_PROMPT,
                f"Discovered resources: {json.dumps(resources)}",
                f"User goal: {user_goal}",
            ],
            config=GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        return json.loads(response.text or "")

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
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        return (response.text or "").strip()

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
PhysMCPGeminiAgent = CP2N2GeminiAgent


def main() -> None:
    user_goal = (
        "Prepare a dry-run plan for the fixed Cortical Labs pattern-"
        "discrimination preset and report whether it is currently admissible."
    )
    result = CP2N2GeminiAgent().run(user_goal)
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
