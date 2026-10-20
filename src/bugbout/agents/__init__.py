"""Adapters that drive coding-agent CLIs headlessly, and the registry that names them."""

from bugbout.agents.base import AgentRun, CodingAgent, Identity, Invocation, Session
from bugbout.agents.claude_code import ClaudeCode
from bugbout.agents.codex import Codex
from bugbout.agents.command import CommandAgent
from bugbout.agents.gemini_cli import GeminiCli
from bugbout.agents.mini_swe import MiniSweAgent
from bugbout.agents.registry import agent_types, create_agent
from bugbout.agents.vendors import vendor_of

__all__ = [
    "AgentRun",
    "ClaudeCode",
    "Codex",
    "CodingAgent",
    "CommandAgent",
    "GeminiCli",
    "Identity",
    "Invocation",
    "MiniSweAgent",
    "Session",
    "agent_types",
    "create_agent",
    "vendor_of",
]
