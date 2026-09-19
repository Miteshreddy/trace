from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class ProviderHealth:
    configured: bool = False
    status: Literal["healthy", "degraded", "unhealthy", "unconfigured"] = "unconfigured"
    requests: int = 0
    successes: int = 0
    failures: int = 0
    latency_ms: float = 0.0
    last_error: str = ""
    last_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "status": self.status,
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "latency_ms": round(self.latency_ms, 1),
            "last_error": self.last_error,
            "last_used": self.last_used,
        }


class AIProvider(ABC):
    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model
        self.health = ProviderHealth()

    @abstractmethod
    def is_configured(self) -> bool:
        """Check whether the API key is present and configured."""
        ...

    def record_success(self, latency_ms: float) -> None:
        self.health.requests += 1
        self.health.successes += 1
        # Exponential moving average for latency
        if self.health.latency_ms == 0.0:
            self.health.latency_ms = latency_ms
        else:
            self.health.latency_ms = 0.7 * self.health.latency_ms + 0.3 * latency_ms
        self.health.status = "healthy"
        self.health.last_used = datetime.now(timezone.utc).isoformat()

    def record_failure(self, error_msg: str) -> None:
        self.health.requests += 1
        self.health.failures += 1
        self.health.last_error = error_msg[:200]
        self.health.status = "degraded" if self.health.successes > 0 else "unhealthy"
        self.health.last_used = datetime.now(timezone.utc).isoformat()

    @abstractmethod
    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        """Generate the next navigation action based on screenshot and UI context."""
        ...

    async def decide_action_async(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        """Asynchronous action decision (defaults to thread-pool offload)."""
        import asyncio
        return await asyncio.to_thread(
            self.decide_action, goal, screenshot, ui_map, ax_tree, history, path_hint
        )

    @abstractmethod
    def audit_run(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Perform multimodal audit review and developer remediation recommendations."""
        ...

    async def audit_run_async(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Asynchronous multimodal audit (defaults to thread-pool offload)."""
        import asyncio
        return await asyncio.to_thread(
            self.audit_run, goal, screenshots, trajectory, heuristic_findings
        )

    @abstractmethod
    def ping(self) -> tuple[bool, float, str]:
        """Lightweight connectivity probe. Returns (success, latency_ms, message)."""
        ...

    async def ping_async(self) -> tuple[bool, float, str]:
        """Asynchronous ping."""
        import asyncio
        return await asyncio.to_thread(self.ping)
