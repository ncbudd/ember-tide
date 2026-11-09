"""Cloud accounts (wearable makers' public APIs) and the two generic ways in (webhooks, JSON polling)."""

from __future__ import annotations

import asyncio
import logging

from .base import CHECK_EVERY_S, CloudConnector, CloudError
from .generic import Generic
from .oura import Oura
from .polar import Polar
from .whoop import Whoop
from .withings import Withings

log = logging.getLogger(__name__)
PROVIDERS: list[type[CloudConnector]] = [Oura, Withings, Whoop, Polar]

# Providers looked at and not built, with why (shown in Studio and docs/integrations.md)
NOT_AVAILABLE = [
    {"id": "fitbit", "name": "Fitbit / Pixel Watch", "why": "Fitbit's Web API was switched off in November 2026; its successor, the Google Health API, needs Google's app verification for health data. Use Health Connect on Android instead."},
    {"id": "garmin", "name": "Garmin", "why": "Garmin's Health API is for approved business partners only. Use Health Connect (Android) or Apple Health (iPhone app)."},
    {"id": "samsung", "name": "Samsung Galaxy Watch", "why": "No public cloud API; Samsung Health shares through Health Connect on Android."},
    {"id": "apple", "name": "Apple Watch", "why": "Apple Health has no cloud API; it's read on the iPhone itself (the iPhone app)."},
]


class CloudAccounts:
    def __init__(self, controller) -> None:
        self.c = controller
        self.providers: dict[str, CloudConnector] = {P.id: P(controller) for P in PROVIDERS}
        self.generic = Generic(controller)
        self._task: asyncio.Task | None = None

    def get(self, pid: str) -> CloudConnector:
        return self.providers[pid]

    async def tick(self) -> None:
        for p in self.providers.values():
            await p.tick()
        await self.generic.tick()

    def start(self) -> None:
        async def loop():
            while True:
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("cloud polling failed")
                await asyncio.sleep(CHECK_EVERY_S)
        self._task = asyncio.get_running_loop().create_task(loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


__all__ = ["CloudAccounts", "CloudConnector", "CloudError", "NOT_AVAILABLE", "PROVIDERS"]
