"""Simple model defaults follow profile names; custom routing labels remain independent."""

# ruff: noqa: F811
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import setup, add
from tests.test_creative_models import group


def test_profile_rename_syncs_simple_default_not_custom_alias(studio):
    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    profile = add(client, root, channel, name="Old image name", model="old-image")
    simple = group(client, root, [profile], name="Old image name", is_default=True)
    alias = group(client, root, [profile], name="My art direction")
    result = client.put(
        f"{root}/model-channels/{channel}/models/{profile['id']}",
        json=dict(
            name="New image name",
            model="new-image",
            protocol="gpt_image",
            modality="image",
            revision=profile["revision"],
        ),
    )
    assert result.status_code == 200, result.text
    catalog = {m["id"]: m for m in client.get(root + "/creative-models").json()}
    assert catalog[simple["id"]]["name"] == "New image name"
    assert catalog[simple["id"]]["is_default"]
    assert catalog[simple["id"]]["revision"] == simple["revision"] + 1
    assert catalog[alias["id"]]["name"] == "My art direction"
    assert catalog[alias["id"]]["revision"] == alias["revision"]


def test_profile_type_change_disables_obsolete_default(studio):
    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    profile = add(client, root, channel, name="Model", model="image-one")
    simple = group(client, root, [profile], name="Model", is_default=True)
    result = client.put(
        f"{root}/model-channels/{channel}/models/{profile['id']}",
        json=dict(
            name="Text model",
            model="text-one",
            protocol="cloud_llm",
            modality="text",
            revision=profile["revision"],
        ),
    )
    assert result.status_code == 200, result.text
    model = next(m for m in client.get(root + "/creative-models").json() if m["id"] == simple["id"])
    assert not model["enabled"] and not model["is_default"]
