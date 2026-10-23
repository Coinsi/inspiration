import pytest
from pydantic import ValidationError
from app.modules.prompt.schemas import PromptIn
from tests.test_media_workflow import studio  # noqa: F401

@pytest.mark.parametrize('value', ['javascript:alert(1)', 'data:image/svg+xml,<svg/>', 'https://user:password@example.com/x', 'data:image/jpeg;base64,invalid'])
def test_cover_rejects_unsafe_inputs(value):
    with pytest.raises(ValidationError):
        PromptIn(name='Cover', cover_image=value)


def test_cover_roundtrip(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    image = 'https://example.com/effect.jpg'
    for resource, fields in [('prompts', {'positive': 'Rainy street'}), ('prompt-fragments', {'text': 'Soft light', 'category': 'lighting'})]:
        response = client.post(f'{root}/{resource}', json={'name': 'Cover', 'cover_image': image, **fields})
        assert response.status_code == 200, response.text
        item = response.json()
        assert item['cover_image'] == image
        assert client.get(f'{root}/{resource}').json()[0]['cover_image'] == image
        body = {'name': 'Updated', **fields}
        if resource == 'prompt-fragments': body['expected_updated_at'] = item['updated_at']
        response = client.patch(f"{root}/{resource}/{item['id']}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()['cover_image'] == image
        body['cover_image'] = ''
        if resource == 'prompt-fragments': body['expected_updated_at'] = response.json()['updated_at']
        response = client.patch(f"{root}/{resource}/{item['id']}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()['cover_image'] == ''
    skill = {'name': 'Skill', 'description': 'Test', 'instructions': 'Test', 'cover_image': image}
    response = client.post(root + '/skills', json=skill)
    assert response.status_code == 200, response.text
    item = response.json()
    assert client.get(root + '/skills/catalog').json()['items'][0]['cover_image'] == image
    assert client.get(root + '/skills/' + item['id']).json()['cover_image'] == image
    response = client.put(root + '/skills/' + item['id'], json={**skill, 'revision': item['revision'], 'cover_image': ''})
    assert response.status_code == 200, response.text
    assert response.json()['cover_image'] == ''
