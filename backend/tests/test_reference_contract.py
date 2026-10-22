"""Reference validation respects each provider input capability independently."""

import pytest

from app.adapters.contracts import Capabilities, ReferenceImage, reference_error


@pytest.mark.parametrize(
    "features,role",
    [({"img2img"}, "ref"), ({"reference"}, "reference"),
     ({"first_frame"}, "first_frame"), ({"last_frame"}, "last_frame")],
)
def test_image_input_uses_its_declared_capability(features, role):
    caps = Capabilities(features=features, max_reference_images=1)
    ref = ReferenceImage(blob_hash="image", role=role)
    assert reference_error([ref], caps) == ""
    assert reference_error([ref, ref], caps)  # Every input still obeys the image limit.


@pytest.mark.parametrize(
    "features,role",
    [(set(), "ref"), ({"first_frame"}, "ref"), ({"img2img"}, "first_frame"),
     ({"reference"}, "last_frame")],
)
def test_image_count_does_not_grant_missing_capabilities(features, role):
    caps = Capabilities(features=features, max_reference_images=1)
    assert reference_error([ReferenceImage(blob_hash="image", role=role)], caps)


def test_image_capabilities_do_not_grant_video_input():
    caps = Capabilities(features={"img2img", "reference"}, max_reference_videos=1)
    assert reference_error([ReferenceImage(blob_hash="video", kind="video")], caps)
