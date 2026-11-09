"""Application-layer authorization for constrained MCP tools."""

from __future__ import annotations

from enum import Enum

from mcp_surface.models import MCPPrincipal


class Scope(str, Enum):
    RESOURCES_READ = "resources:read"
    LEASES_WRITE = "leases:write"
    ASSAYS_PREPARE = "assays:prepare"
    ASSAYS_EXECUTE = "assays:execute"
    RUNS_ABORT = "runs:abort"


class AuthorizationDenied(PermissionError):
    """Raised when the server-authenticated principal lacks a tool scope."""


class StaticAuthorizer:
    """Authorize against immutable scopes attached to the server principal."""

    def __init__(self, principal: MCPPrincipal) -> None:
        self.principal = principal
        self._scopes = set(principal.scopes)

    def require(self, scope: Scope) -> None:
        if not self.principal.authenticated:
            raise AuthorizationDenied("The MCP principal is not authenticated.")
        if scope.value not in self._scopes:
            raise AuthorizationDenied(
                f"Principal '{self.principal.principal_id}' lacks scope '{scope.value}'."
            )
