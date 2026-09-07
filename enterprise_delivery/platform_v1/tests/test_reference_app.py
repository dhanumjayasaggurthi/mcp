from fastapi.testclient import TestClient


def test_reference_app_smoke(monkeypatch):
    monkeypatch.setenv('EDP_REFERENCE_MODE', 'true')
    from enterprise_data_platform.reference_app import app

    client = TestClient(app)
    assert client.get('/v1/health').status_code == 200

    headers = {'x-client-id': 'reference-consumer', 'x-tenant': 'demo'}
    datasets = client.get('/v1/datasets', headers=headers)
    assert datasets.status_code == 200
    assert datasets.json()['datasets'][0]['id'] == 'regulatory-documents'

    query = client.post(
        '/v1/datasets/regulatory-documents/query',
        headers=headers,
        json={'select': ['id', 'title'], 'limit': 10},
    )
    assert query.status_code == 200, query.text
    assert [row['id'] for row in query.json()['rows']] == ['DOC-001', 'DOC-002']

    retrieve = client.post(
        '/v1/datasets/regulatory-documents/retrieve',
        headers=headers,
        json={'query': 'adverse event reporting', 'mode': 'hybrid', 'top_k': 5},
    )
    assert retrieve.status_code == 200, retrieve.text
    assert retrieve.json()['results']
    assert retrieve.json()['results'][0]['text']

    admin = client.get(
        '/v1/control/overview',
        headers={**headers, 'x-client-id': 'control-hub', 'x-groups': 'data-platform-admin'},
    )
    assert admin.status_code == 200, admin.text
    assert admin.json()['indexes'] == 2
