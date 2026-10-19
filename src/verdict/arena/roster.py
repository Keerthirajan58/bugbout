"""Every agent and examiner the arena can field, by short name.

The one place model choices live. Each entry records *why* it is configured the
way it is, because every one of these choices was forced by a measured
free-tier limit or a measured failure - and a reader comparing results needs to
know which.
"""

from __future__ import annotations

from collections.abc import Callable

from verdict.arena.agents import CodexAgent, CodingAgent, GeminiAgent, MiniSweAgent, OllamaServer
from verdict.arena.assignments import Reproduce, Specify
from verdict.arena.examiners import AgentExaminer, Examiner, PromptExaminer
from verdict.config import Settings
from verdict.llm.providers import GeminiProvider, GroqProvider, OllamaProvider

# The only Mistral coding model the free tier serves: mistral-medium-latest
# reports a limit of 0 requests a minute there; codestral-latest allows 125
# requests and 625,000 tokens a minute, with working tool calls (measured).
MISTRAL_MODEL = "codestral-latest"


class UnknownNameError(KeyError):
    pass


class Roster:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._contestants: dict[str, Callable[[], CodingAgent]] = {
            # ChatGPT plan, each model at its default reasoning level.
            "codex": lambda: CodexAgent("gpt-6.1-sol"),
            "codex-mini": lambda: CodexAgent("gpt-5.6-luna"),
            # A third vendor, through a third harness. Tool calls work on the
            # hosted API, so the stock config is used.
            "codestral": lambda: MiniSweAgent(
                f"mistral/{MISTRAL_MODEL}",
                lane="mistral",
                env={"MISTRAL_API_KEY": self._secret("mistral_api_key")},
            ),
            # Free API tier allows ~20 requests/day/model; one task used a day.
            "gemini": lambda: GeminiAgent("gemini-3.8-flash", self._secret("gemini_api_key")),
            # Local open weights. Neither harness completes an agent loop with
            # gpt-oss:20b through Ollama; kept so the measurement is repeatable.
            "oss": lambda: MiniSweAgent(
                "ollama_chat/gpt-oss:20b",
                lane="ollama",
                model_kwargs={"api_base": OllamaServer.base_url()},
                text_actions=True,
            ),
            "codex-oss": lambda: CodexAgent("gpt-oss:20b", local=True),
        }
        self._examiners: dict[str, Callable[[], Examiner]] = {
            "gemini-flash": self._gemini_examiner,
            "qwen": self._qwen_examiner,
            "gpt-oss-local": lambda: PromptExaminer(
                "examiner/gpt-oss-20b",
                [OllamaProvider("gpt-oss:20b", port=OllamaServer.PORT)],
                context_budget=40_000,
            ),
            "codex-reproducer": lambda: AgentExaminer(
                CodexAgent("gpt-6.1-sol"), Reproduce(), lane_slots={"chatgpt": 2}
            ),
            "codex-specifier": lambda: AgentExaminer(
                CodexAgent("gpt-6.1-sol"), Specify(), lane_slots={"chatgpt": 2}
            ),
            # 125 requests a minute; one attempt uses ~40 a minute, so three fit.
            "codestral-reproducer": lambda: AgentExaminer(
                self.contestant("codestral"), Reproduce(), lane_slots={"mistral": 3}
            ),
        }

    @property
    def contestant_names(self) -> tuple[str, ...]:
        return tuple(self._contestants)

    @property
    def examiner_names(self) -> tuple[str, ...]:
        return tuple(self._examiners)

    def contestant(self, name: str) -> CodingAgent:
        if name not in self._contestants:
            raise UnknownNameError(f"{name!r}; choose from {', '.join(self._contestants)}")
        return self._contestants[name]()

    def examiner(self, name: str) -> Examiner:
        if name not in self._examiners:
            raise UnknownNameError(f"{name!r}; choose from {', '.join(self._examiners)}")
        return self._examiners[name]()

    # --- prompted examiners, each shaped by its tier -------------------------------

    def _gemini_examiner(self) -> Examiner:
        # Gemini 3 bills its thinking against the output budget, so the budget
        # is large and thinking is kept low; models are tried in order as each
        # one's ~20 free requests a day run out.
        key = self._secret("gemini_api_key")
        return PromptExaminer(
            "examiner/gemini-flash",
            [
                GeminiProvider(key, model=m, thinking="low")
                for m in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")
            ],
            context_budget=40_000,
            max_output_tokens=16_384,
        )

    def _qwen_examiner(self) -> Examiner:
        # Groq books prompt + max_tokens against 8,000 tokens a minute, so the
        # context is held to what fits beside a 4,096-token answer.
        key = self._secret("groq_api_key")
        return PromptExaminer(
            "examiner/qwen3.8-27b",
            [GroqProvider(key, model=m) for m in ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b")],
            context_budget=9_000,
        )

    def _secret(self, field: str) -> str:
        return self._settings.require(field)
