"""Internal modules for the LLM service exposed by :mod:`llm`.

Submodules are intentionally loaded on demand; importing a prompt helper does
not start the LLM idle watcher or register process shutdown hooks.
"""

__all__ = ["engine", "metrics", "prompting", "transport"]
