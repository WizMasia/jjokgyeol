"""Common selection contract for a measured paragraph."""
from typing import Protocol

from app.configuration import CONFIG_DIR, read_config

LIMITS = read_config(CONFIG_DIR / "decision.json")
Candidates = dict[str, list[dict[str, int | bool | str]]]


class DecisionModel(Protocol):
    def predict(self, options: Candidates, *, timeout: float) -> str:
        """Return one supplied ID within the remaining seconds."""
        ...


class ProviderError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
