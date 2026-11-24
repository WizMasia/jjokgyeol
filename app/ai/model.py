"""Common selection contract for a measured paragraph."""
from typing import Protocol

from app.configuration import CONFIG_DIR, read_config

LIMITS = read_config(CONFIG_DIR / "decision.json")
PARAGRAPH_LEADING_RATIOS = LIMITS.get("paragraph_leading_ratios")
if (not isinstance(PARAGRAPH_LEADING_RATIOS, list) or not PARAGRAPH_LEADING_RATIOS
        or any(type(ratio) is not int or not 1 <= ratio <= (1 << 53) - 1
               for ratio in PARAGRAPH_LEADING_RATIOS)):
    raise ValueError("decision.json paragraph_leading_ratios must be positive exact integers")
Candidates = dict[str, list[dict[str, int | bool | str]]]


SPACING_PROMPT = (
    'Choose the vertical gap between adjacent paragraphs from the provided candidates. '
    'Consider block kinds and levels, body size, remaining page space and line count. '
    'Lengths are in TeX sp. Return only a JSON object {"id":"..."} with the exact candidate ID.'
)
LEADING_PROMPT = (
    'Choose a safe baseline distance for one automatic paragraph from the supplied candidates. '
    'Use only its font size, line count and remaining page space. '
    'Lengths are in TeX sp. Return only a JSON object {"id":"..."} with the exact candidate ID.'
)


class DecisionModel(Protocol):
    def predict(self, options: Candidates, *, timeout: float) -> str:
        """Return one supplied ID within the remaining seconds."""
        ...


class ProviderError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
