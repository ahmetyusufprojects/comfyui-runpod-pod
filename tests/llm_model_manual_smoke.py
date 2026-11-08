"""Verify RunPod exposes configured LLM profiles for explicit selection."""
import sys

sys.path.insert(0, "/opt/webui_flux2")

import pod_setup
import llm


def main():
    profiles = llm.model_profilleri()
    assert profiles and all(item.get("id") for item in profiles), profiles
    assert {item["id"] for item in profiles} == {"llm_26b"}, profiles
    assert not any(item in pod_setup.WORKFLOWS for item in ("llama", "llm_12b"))

    print("LLM manual model selection smoke passed")


if __name__ == "__main__":
    main()
