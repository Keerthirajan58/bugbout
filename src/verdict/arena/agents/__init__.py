"""Adapters for the coding-agent CLIs the arena can drive."""

from verdict.arena.agents.base import CodingAgent, RunRecord
from verdict.arena.agents.codex import CodexAgent
from verdict.arena.agents.gemini import GeminiAgent
from verdict.arena.agents.mini_swe import MiniSweAgent
from verdict.arena.agents.ollama import OllamaServer

__all__ = ["CodexAgent", "CodingAgent", "GeminiAgent", "MiniSweAgent", "OllamaServer", "RunRecord"]
