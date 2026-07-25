"""Answer providers. Phase 0 ships only the mock; the real (Voyage + Claude)
provider lands in Phase 4.

The mock is loud on purpose: every answer it returns is clearly labeled as a
fallback so a mock answer can never be mistaken for a real, grounded one.
"""

from __future__ import annotations

from knowledge_desk.config import settings

MOCK_BANNER = "[MOCK FALLBACK] no provider keys set; this answer is not grounded."


class MockProvider:
    name = "mock"

    def answer(self, question: str) -> str:
        return f"{MOCK_BANNER} You asked: {question}"


def get_provider():
    """Return the active provider. Phase 0 always resolves to the mock; the
    real branch is added in Phase 4. PROVIDER_STRICT is honored at config time.
    """
    if settings.provider == "real":
        # Placeholder until Phase 4 wires Voyage + Claude.
        raise NotImplementedError("real provider arrives in Phase 4")
    return MockProvider()
