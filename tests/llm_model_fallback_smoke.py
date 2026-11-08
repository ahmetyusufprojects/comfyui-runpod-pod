"""Verify the selected Gemma falls back only toward smaller profiles."""
import sys

sys.path.insert(0, "/opt/webui_flux2")

import pod_setup


def main():
    profiles = ("llm_26b", "llm_12b", "llama")
    for index, preferred in enumerate(profiles):
        assert pod_setup.llm_profile_fallback(preferred, lambda _: True) == preferred
        for ready_index in range(index + 1, len(profiles)):
            available = {profiles[ready_index]}
            assert pod_setup.llm_profile_fallback(
                preferred, lambda name, available=available: name in available
            ) == profiles[ready_index]
        assert pod_setup.llm_profile_fallback(preferred, lambda _: False) is None

    try:
        pod_setup.llm_profile_fallback("unknown", lambda _: True)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown LLM profile was accepted")

    print("LLM model fallback smoke passed")


if __name__ == "__main__":
    main()
