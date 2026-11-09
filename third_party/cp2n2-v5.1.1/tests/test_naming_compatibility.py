"""Compatibility checks for the CP²N² naming migration."""

from agent.ai_lab_agent import CP2N2AILabAgent, PhysMCPAILabAgent
from agent.gemini_agent import CP2N2GeminiAgent, PhysMCPGeminiAgent
from agent.ollama_agent import CP2N2OllamaAgent, PhysMCPOllamaAgent
from core.orchestrator import CP2N2Orchestrator, PhysMCPOrchestrator
from mcp_surface.server import _configuration_value


def test_pre_rename_class_names_remain_aliases() -> None:
    assert PhysMCPOrchestrator is CP2N2Orchestrator
    assert PhysMCPGeminiAgent is CP2N2GeminiAgent
    assert PhysMCPOllamaAgent is CP2N2OllamaAgent
    assert PhysMCPAILabAgent is CP2N2AILabAgent


def test_pre_rename_environment_names_remain_fallbacks(monkeypatch) -> None:
    monkeypatch.delenv("CP2N2_TEST_VALUE", raising=False)
    monkeypatch.setenv("PHYSMCP_TEST_VALUE", "legacy")
    assert (
        _configuration_value("CP2N2_TEST_VALUE", "PHYSMCP_TEST_VALUE")
        == "legacy"
    )

    monkeypatch.setenv("CP2N2_TEST_VALUE", "canonical")
    assert (
        _configuration_value("CP2N2_TEST_VALUE", "PHYSMCP_TEST_VALUE")
        == "canonical"
    )
