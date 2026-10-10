"""Exact-pin OWLv2 microbatches inside optional inference children only."""

import ctypes
import functools
import hashlib
import inspect
from pathlib import Path

from wise_bridge import MODELS, WISE_COMMIT

EXTRACTION_SHA256 = "3951cedb285bf6b11f42c426bf632eeb127be38969f394657f4821a9269be420"
PATCH_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"


@functools.lru_cache(maxsize=1)
def _cuda_driver():
    driver = ctypes.CDLL("libcuda.so.1")
    driver.cuInit.argtypes, driver.cuInit.restype = [ctypes.c_uint], ctypes.c_int
    driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    driver.cuDeviceGetCount.restype = ctypes.c_int
    return driver


def cuda_driver_device_count():
    """Cheap driver-level readiness, no torch import or model/GPU allocations."""
    try:
        driver = _cuda_driver()
        count = ctypes.c_int()
        if driver.cuInit(0) != 0 or driver.cuDeviceGetCount(ctypes.byref(count)) != 0:
            return 0
        return max(0, count.value)
    except (OSError, AttributeError):
        return 0


def gpu_device_nodes_available():
    # Docker Desktop's WSL2 CUDA passthrough exposes /dev/dxg instead of the
    # native Linux NVIDIA nodes. This is only a cheap preflight; the inference
    # child still checks torch.cuda.is_available and its real extractor device.
    return Path("/dev/dxg").exists() or (
        Path("/dev/nvidiactl").exists() and any(Path("/dev").glob("nvidia[0-9]*"))
    )


def check_device_configuration(config):
    """Standard-library preflight; never import torch during an HTTP check."""
    from wise_bridge.optional_models import OptionalModelUnavailable

    if not isinstance(config, dict):
        raise OptionalModelUnavailable("object", "model_device_unspecified")
    effective = {**config.get("default", {}), **config.get(MODELS["object"], {})}
    device = effective.get("device")
    if device not in {"cpu", "cuda", "cuda:0"} or effective.get("url") is not None:
        raise OptionalModelUnavailable("object", "model_device_unspecified")
    if device.startswith("cuda") and (
        not gpu_device_nodes_available() or cuda_driver_device_count() < 1
    ):
        raise OptionalModelUnavailable("object", "model_device_unavailable")
    return device


def microbatch_method(original):
    if (
        WISE_COMMIT != PATCH_COMMIT
        or hashlib.sha256(inspect.getsource(original).encode()).hexdigest()
        != EXTRACTION_SHA256
    ):
        raise RuntimeError("WISE object microbatch source changed; review required")

    @functools.wraps(original)
    def one_frame(self, images):
        # Invalid inputs still reach the original validation. The known upstream
        # method returns one Features object per batch item, in original order.
        shape = getattr(images, "shape", ())
        if len(shape) != 4 or shape[0] <= 1:
            return original(self, images)
        output = []
        for index in range(shape[0]):
            result = original(self, images[index : index + 1])
            if not isinstance(result, list) or len(result) != 1:
                raise RuntimeError("WISE object feature result structure changed")
            output.extend(result)
        return output

    one_frame._inspiration_object_microbatch = 1
    return one_frame


def install_object_runtime():
    """Never call in a HTTP process; real device validation imports the ML stack."""
    from wise_bridge.optional_attestation import runtime_identity
    from wise_bridge.optional_models import OptionalModelUnavailable

    device = check_device_configuration(runtime_identity()["extractor_config"])
    import torch
    from wise.feature.feature_extractor_factory import get_feature_extractor_class

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise OptionalModelUnavailable("object", "model_device_unavailable")
    cls = get_feature_extractor_class(MODELS["object"])
    original = cls.extract_image_features
    if getattr(original, "_inspiration_object_microbatch", None) != 1:
        # This class exists only in this isolated inference child. The fixed
        # checkout is unchanged, and API/default visual/audio processes do not
        # install this wrapper. Query/probe/extraction all install the same one.
        cls.extract_image_features = microbatch_method(original)
    region_original = cls.extract_image_region_features
    if getattr(region_original, "_inspiration_object_region", None) != 1:
        from wise_bridge.object_query import region_method

        cls.extract_image_region_features = region_method(
            region_original, cls._extract_image_region_features_highest_iou
        )
