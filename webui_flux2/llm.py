"""Stable compatibility facade for the modular local LLM runtime."""

import sys as _sys
from llm_runtime import engine as _engine

# Keep the historical module identity. Callers that mutate module settings
# (for example ``llm.SISTEM_PROMPTU``) must reach the live engine state.
_sys.modules[__name__] = _engine
