"""Test config for the AI service. Set env BEFORE app import.

`app.config` reads these at import time. Forcing the mock provider keeps tests
hermetic (no real LLM calls) even if the loaded .env carries real API keys.
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SIGNING_KEY", "test-signing-key")
os.environ.setdefault("LLM_PROVIDER_ORDER", "mock")   # never call a real LLM in tests
