"""Each adapter starts its CLI from a clean configuration, confined, and reads its logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import override

import pytest

from bugbout.agents import ClaudeCode, Codex, CommandAgent, GeminiCli, MiniSweAgent, Session
from bugbout.agents.mini_swe import PLUGINS, SANDBOX_PREFIX_ENV
from bugbout.errors import ConfigError
from bugbout.sandbox import Confinement, Sandbox


class RecordingSandbox(Sandbox):
    """Prefixes commands with a marker and remembers what each was allowed."""

    name = "recording"

    def __init__(self) -> None:
        self.confinements: list[Confinement] = []

    @classmethod
    @override
    def problem(cls) -> str | None:
        return None

    @override
    def prefix(self, confinement: Confinement) -> list[str]:
        self.confinements.append(confinement)
        return ["sandboxed", "--"]


@pytest.fixture
def sandbox() -> RecordingSandbox:
    return RecordingSandbox()


@pytest.fixture
def session(tmp_path: Path, sandbox: RecordingSandbox) -> Session:
    (tmp_path / "repo").mkdir()
    (tmp_path / "logs").mkdir()
    return Session("Fix the bug.", tmp_path / "repo", tmp_path / "logs", sandbox)


def _option(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class TestCodex:
    def test_it_starts_from_a_clean_config_in_its_own_sandbox(self, session: Session) -> None:
        invocation = Codex("gpt-5.1-codex").invocation(session)
        argv = invocation.argv
        assert argv[1] == "exec" and invocation.stdin == "Fix the bug." and argv[-1] == "-"
        assert {"--ignore-user-config", "--ignore-rules", "--ephemeral"} <= set(argv)
        assert _option(argv, "--sandbox") == "workspace-write"
        assert _option(argv, "--cd") == str(session.workdir)
        assert 'web_search="disabled"' in argv and "features.hooks=false" in argv
        assert "--disable" not in argv  # an unknown feature name would stop Codex starting

    def test_reasoning_effort_and_local_models_are_opt_in(self, session: Session) -> None:
        argv = (
            Codex("gpt-oss:20b", reasoning_effort="high", local_provider="ollama")
            .invocation(session)
            .argv
        )
        assert 'model_reasoning_effort="high"' in argv
        assert _option(argv, "--local-provider") == "ollama" and "--oss" in argv

    def test_without_a_model_the_cli_s_default_is_used(self, session: Session) -> None:
        agent = Codex()
        assert "--model" not in agent.invocation(session).argv
        assert (agent.name, agent.vendor) == ("codex", "openai")

    def test_identity_defaults(self) -> None:
        agent = Codex("gpt-5.1-codex")
        assert (agent.name, agent.vendor, agent.quota) == ("codex/gpt-5.1-codex", "openai", "codex")
        assert Codex("qwen3-coder", local_provider="ollama").quota == "ollama"
        assert Codex("gpt-5", name="mine", quota="team").name == "mine"

    def test_it_reads_the_last_message_and_sums_usage(self, tmp_path: Path) -> None:
        (tmp_path / "last_message.txt").write_text("Fixed it.\n")
        events = [
            {"usage": {"input_tokens": 10, "output_tokens": 2}},
            {"usage": {"input_tokens": 5}},
        ]
        (tmp_path / "stdout.log").write_text("\n".join(json.dumps(e) for e in events))
        assert Codex("gpt-5").parse(tmp_path) == (
            "Fixed it.",
            {"input_tokens": 15, "output_tokens": 2},
        )


class TestClaudeCode:
    def test_it_runs_in_safe_mode_and_never_asks(self, session: Session) -> None:
        invocation = ClaudeCode("sonnet").invocation(session)
        argv = invocation.argv
        assert "--print" in argv and invocation.stdin == "Fix the bug."
        assert {"--safe-mode", "--no-session-persistence"} <= set(argv)
        assert _option(argv, "--permission-prompts") == "none"
        assert _option(argv, "--permission-mode") == "acceptEdits"
        assert _option(argv, "--disallowedTools") == "WebFetch,WebSearch"
        sandbox = json.loads(_option(argv, "--settings"))["sandbox"]
        assert sandbox["enabled"] and sandbox["failIfUnavailable"]
        assert not sandbox["allowUnsandboxedCommands"]

    def test_it_never_joins_the_claude_code_session_that_started_bugbout(
        self, session: Session
    ) -> None:
        unset = ClaudeCode("sonnet").invocation(session).unset
        assert {"CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"} <= set(
            unset
        )
        assert "CLAUDE_CODE_USE_BEDROCK" not in unset  # the user's own configuration stays

    def test_an_alias_still_belongs_to_anthropic(self) -> None:
        assert ClaudeCode("sonnet").vendor == "anthropic"

    def test_it_reads_the_result_event(self, tmp_path: Path) -> None:
        events = [
            {"type": "assistant", "message": {}},
            {"type": "result", "result": "Done.", "num_turns": 7, "usage": {"output_tokens": 9}},
        ]
        (tmp_path / "stdout.log").write_text("\n".join(json.dumps(e) for e in events))
        assert ClaudeCode("sonnet").parse(tmp_path) == ("Done.", {"output_tokens": 9, "turns": 7})


class TestGeminiCli:
    def test_the_whole_process_runs_in_bugbout_s_sandbox(
        self, session: Session, sandbox: RecordingSandbox
    ) -> None:
        invocation = GeminiCli("gemini-2.5-pro").invocation(session)
        assert invocation.argv[:2] == ["sandboxed", "--"]
        assert invocation.env == {"GEMINI_SANDBOX": "false"}
        [confinement] = sandbox.confinements
        assert confinement.network and session.workdir in confinement.writable
        assert Path.home() / ".gemini" in confinement.writable

    def test_an_api_error_becomes_the_summary(self, tmp_path: Path) -> None:
        event = {"type": "result", "error": {"message": "model retired"}, "stats": {"tokens": 0}}
        (tmp_path / "stdout.log").write_text(json.dumps(event))
        assert GeminiCli("gemini-x").parse(tmp_path) == ("model retired", {"tokens": 0})

    def test_web_tools_are_excluded_in_workspace_settings(self, tmp_path: Path) -> None:
        GeminiCli("gemini-2.5-pro").prepare(tmp_path)
        settings = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert settings["tools"]["exclude"] == ["web_fetch", "google_web_search"]


class TestMiniSweAgent:
    def test_every_command_is_sandboxed_without_network(
        self, session: Session, sandbox: RecordingSandbox
    ) -> None:
        invocation = MiniSweAgent("ollama/qwen3-coder:30b").invocation(session)
        assert json.loads(invocation.env[SANDBOX_PREFIX_ENV]) == ["sandboxed", "--"]
        assert invocation.env["PYTHONPATH"].split(":")[0] == str(PLUGINS)
        [confinement] = sandbox.confinements
        assert not confinement.network
        assert confinement.writable == (session.workdir, session.logdir)
        assert "bugbout_mini_env.SandboxedEnvironment" in invocation.argv

    def test_text_actions_switch_model_class_and_config(self, session: Session) -> None:
        argv = MiniSweAgent("ollama/qwen3", text_actions=True).invocation(session).argv
        assert _option(argv, "--model-class") == "litellm_textbased"
        assert "mini_textbased.yaml" in argv

    def test_a_model_is_required(self) -> None:
        with pytest.raises(ConfigError, match="needs a model"):
            MiniSweAgent("")

    def test_the_quota_is_the_provider(self) -> None:
        assert MiniSweAgent("mistral/codestral-latest").quota == "mistral"
        assert MiniSweAgent("mistral/codestral-latest").vendor == "mistral"


class TestCommandAgent:
    def test_placeholders_are_filled_and_braces_kept(self, session: Session) -> None:
        agent = CommandAgent(
            ["aider", "--message-file", "{prompt_file}", "--model", "{model}", '{"a": 1}'],
            name="aider",
            model="gpt-5",
            sandboxed=False,
        )
        invocation = agent.invocation(session)
        assert invocation.argv[1:] == [
            "--message-file",
            str(session.logdir / "prompt.md"),
            "--model",
            "gpt-5",
            '{"a": 1}',
        ]
        assert invocation.stdin is None
        assert (session.logdir / "prompt.md").read_text() == "Fix the bug."

    def test_without_a_prompt_placeholder_the_prompt_goes_to_stdin(self, session: Session) -> None:
        agent = CommandAgent(["my-agent"], name="mine", sandboxed=False)
        assert agent.invocation(session).stdin == "Fix the bug."

    def test_the_process_is_sandboxed_with_network_for_its_model(
        self, session: Session, sandbox: RecordingSandbox
    ) -> None:
        agent = CommandAgent(["my-agent"], name="mine", state_dirs=["~/.my-agent"])
        assert agent.invocation(session).argv[:2] == ["sandboxed", "--"]
        [confinement] = sandbox.confinements
        assert confinement.network
        assert confinement.writable[-1] == Path.home() / ".my-agent"

    def test_identity_comes_from_the_name_when_there_is_no_model(self) -> None:
        agent = CommandAgent(["my-agent"], name="mine")
        assert (agent.name, agent.vendor, agent.quota) == ("mine", "mine", "mine")
