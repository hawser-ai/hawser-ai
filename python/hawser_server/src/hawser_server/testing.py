"""Test helpers that are also useful for integration scripts: a live server and polling."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from hawser.gateway import Gateway, load_registry
from hawser.testing import StaticAdapter
from hawser_server.services import Services

ServicesFactory = Callable[..., Services]


def wait_for(predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def static_gateway(output: Any) -> Gateway:
    """A gateway whose Anthropic adapter returns ``output`` for every call."""
    return Gateway(load_registry(), {"anthropic": StaticAdapter(output)})


class LiveServer:
    """``hawserd`` on a free localhost port in a background thread."""

    def __init__(self, services: Services) -> None:
        import uvicorn  # noqa: PLC0415 - heavy import

        from hawser_server.app import create_app  # noqa: PLC0415

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.url = f"http://127.0.0.1:{self.port}"
        services.config.public_url = self.url
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(services), host="127.0.0.1", port=self.port, log_level="warning"
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> LiveServer:
        self._thread.start()
        wait_for(lambda: self._server.started, timeout=15)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


__all__ = ["LiveServer", "ServicesFactory", "static_gateway", "wait_for"]
