"""Official MCP 1.x low-level server binding for the constrained A4 surface."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from demos.common import build_live_target_orchestrator
from mcp_surface.audit import JsonlHashChainAuditTrail
from mcp_surface.models import MCPPrincipal, ToolResponse
from mcp_surface.service import MCPControlSurface, TOOL_SPECS


SERVER_NAME = "cp2n2-constrained"
SERVER_VERSION = "0.4.0"
SERVER_INSTRUCTIONS = (
    "Use only the listed high-level CP²N² tools. Physical control parameters, "
    "policy mutation, runtime-kind mutation, lease bypass, and repeated execution "
    "loops are intentionally unavailable. Real biological execution requires an "
    "external one-time human approval."
)


def _configuration_value(
    name: str,
    legacy_name: str,
    default: str = "",
) -> str:
    """Read a CP²N² setting with a pre-rename environment fallback."""
    return os.getenv(name, os.getenv(legacy_name, default))


def create_mcp_server(surface: MCPControlSurface) -> Server:
    """Create a protocol server exposing exactly the ten A4 tools."""
    server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_model.model_json_schema(),
                outputSchema=ToolResponse.model_json_schema(),
                annotations=types.ToolAnnotations(
                    title=spec.name.replace("_", " ").title(),
                    readOnlyHint=spec.read_only,
                    destructiveHint=spec.destructive,
                    idempotentHint=spec.idempotent,
                    openWorldHint=False,
                ),
            )
            for spec in TOOL_SPECS.values()
        ]

    # Validation is deliberately performed inside MCPControlSurface so malformed
    # calls are written to the append-only audit trail before being rejected.
    @server.call_tool(validate_input=False)
    async def call_tool(
        name: str,
        arguments: dict | None,
    ) -> dict:
        response = surface.invoke(name, arguments)
        return response.model_dump(mode="json")

    return server


def build_default_surface() -> MCPControlSurface:
    """Build a fail-closed stdio surface from server-controlled environment."""
    principal_id = _configuration_value(
        "CP2N2_PRINCIPAL_ID",
        "PHYSMCP_PRINCIPAL_ID",
        "unauthenticated",
    )
    scopes = [
        item.strip()
        for item in _configuration_value(
            "CP2N2_SCOPES",
            "PHYSMCP_SCOPES",
        ).split(",")
        if item.strip()
    ]
    principal = MCPPrincipal(
        principal_id=principal_id,
        authenticated=principal_id != "unauthenticated",
        scopes=scopes,
    )
    include_cortical = (
        _configuration_value(
            "CP2N2_INCLUDE_CORTICAL_LABS",
            "PHYSMCP_INCLUDE_CORTICAL_LABS",
            "0",
        )
        == "1"
    )
    include_biopattern_gate_e3 = (
        _configuration_value(
            "CP2N2_INCLUDE_BIOPATTERN_GATE_E3",
            "PHYSMCP_INCLUDE_BIOPATTERN_GATE_E3",
            "0",
        )
        == "1"
    )
    orchestrator = build_live_target_orchestrator(
        include_cortical_labs=include_cortical,
        include_biopattern_gate_e3=include_biopattern_gate_e3,
    )
    audit_path = Path(
        _configuration_value(
            "CP2N2_AUDIT_PATH",
            "PHYSMCP_AUDIT_PATH",
            str(Path(".cp2n2") / "mcp-audit.jsonl"),
        )
    )
    return MCPControlSurface(
        orchestrator=orchestrator,
        principal=principal,
        audit_trail=JsonlHashChainAuditTrail(audit_path),
    )


async def run_stdio() -> None:
    surface = build_default_surface()
    server = create_mcp_server(surface)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
                instructions=SERVER_INSTRUCTIONS,
            ),
        )


def main() -> None:
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
