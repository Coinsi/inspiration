"""Optional extract CLI guard; no flags can bypass missing model acceptance."""

import json
import runpy
import sys

from wise_bridge import MODELS
from wise_bridge.optional_models import enforce_optional_runtime


def main():
    capabilities = json.loads(sys.argv[1])
    if not isinstance(capabilities, list) or not all(
        isinstance(cap, str) for cap in capabilities
    ):
        raise ValueError("Invalid capability list")
    if sys.argv[2] != "--":
        raise ValueError("Expected -- before WISE CLI arguments")
    if not set(capabilities) <= set(MODELS):
        raise ValueError("Unknown capability")
    # Never let a caller label a face extraction as visual to bypass the gate.
    arguments = {argument.split("=", 1)[-1] for argument in sys.argv[3:]}
    actual = {cap for cap, model in MODELS.items() if model in arguments}
    enforce_optional_runtime(set(capabilities) | actual)
    sys.argv = ["wise", *sys.argv[3:]]
    runpy.run_module("wise", run_name="__main__")


if __name__ == "__main__":
    main()
