"""A private Ollama server for local models, with a context window an agent fits in.

Ollama sizes its default context from available VRAM, and on a 24 GB laptop
that default is 4,096 tokens - less than a coding agent's system prompt and
tool definitions, before the task has even been read. Rather than change the
user's own Ollama service, the arena runs its own on a separate port and stops
it afterwards. Models are shared on disk, so nothing is downloaded twice.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request

from verdict.arena.agents.base import binary


class OllamaServer:
    PORT = 11435
    CONTEXT = 65536

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    @classmethod
    def base_url(cls) -> str:
        return f"http://127.0.0.1:{cls.PORT}"

    def _up(self) -> bool:
        try:
            # A literal loopback URL; nothing here comes from input.
            with urllib.request.urlopen(f"{self.base_url()}/api/version", timeout=2):  # noqa: S310
                return True
        except OSError:
            return False

    def __enter__(self) -> OllamaServer:
        if self._up():
            return self
        env = {
            **os.environ,
            "OLLAMA_HOST": f"127.0.0.1:{self.PORT}",
            "OLLAMA_CONTEXT_LENGTH": str(self.CONTEXT),
            "OLLAMA_KEEP_ALIVE": "30m",
        }
        self._proc = subprocess.Popen(  # noqa: S603 - the resolved ollama binary, literal args
            [binary("ollama"), "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(60):
            if self._up():
                return self
            time.sleep(0.5)
        raise RuntimeError("the arena's Ollama server did not come up")

    def __exit__(self, *_: object) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc.wait(timeout=30)
