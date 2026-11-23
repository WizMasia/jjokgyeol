"""Run the first-party model inside the render worker."""
from copy import deepcopy
from importlib.util import find_spec
from time import monotonic

from app.ai.model import Candidates, LIMITS, ProviderError


class BuiltinDecision:
    @classmethod
    def installed(cls):
        if find_spec("app.ai.trained_decision") is None:
            raise ProviderError("builtin_unavailable")
        return cls()

    def predict(self, options: Candidates, *, timeout=None) -> str:
        seconds = LIMITS["decision_seconds"]
        if timeout is not None:
            seconds = min(seconds, timeout)
        end = monotonic() + seconds
        if seconds <= 0:
            raise ProviderError("timeout")
        try:
            # Import only after the rule PDF is ready; packaging discovers this fixed module.
            from app.ai.trained_decision import predict

            candidates = deepcopy(options)
            remaining = end - monotonic()
            if remaining <= 0:
                raise TimeoutError
            selected = predict(candidates, timeout=remaining)
        except TimeoutError:
            raise ProviderError("timeout") from None
        except (ImportError, FileNotFoundError):
            raise ProviderError("builtin_unavailable") from None
        except Exception:
            raise ProviderError("invalid_response") from None
        if monotonic() >= end:
            raise ProviderError("timeout")
        if not isinstance(selected, str) or selected not in options:
            raise ProviderError("invalid_response")
        return selected
