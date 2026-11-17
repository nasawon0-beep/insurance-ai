from test_coverage_analysis_api import client


def test_same_document_reimport_preserves_review_decision(client, monkeypatch):
    from database import coverage
    monkeypatch.setattr(coverage, 'rag_search', lambda *a, **kw: {'hits': []})
    customer = client.post('/customers', json={'name': 'SYNTHETIC-REIMPORT'}).json()['id']
    path = f'/customers/{customer}/coverage-analysis'
    def run(source):
        r = client.post(path + '/recalculate', json={'force': True, 'source_hash': source})
        assert r.status_code == 200
        return client.get(path, params={'audience': 'internal', 'run_id': r.json()['run_id']}).json()
    first = run('synthetic-source-a')
    item = first['categories'][0]['items'][0]
    r = client.patch(path + '/' + item['id'], json={
        'current_amount': 123456, 'internal_memo': 'SYNTHETIC-CONFIRMED',
        'override_reason': 'SYNTHETIC-REVIEW', 'reviewed_by': 'FIXTURE-REVIEWER',
    })
    assert r.status_code == 200
    repeated = run('synthetic-source-a')
    review = next(i for c in repeated['categories'] for i in c['items'] if i['coverage_name'] == item['coverage_name'])
    assert review['current_amount'] == 123456
    assert review['manual_override'] is True
    assert review['internal_memo'] == 'SYNTHETIC-CONFIRMED'
    assert review['override_reason'] == 'SYNTHETIC-REVIEW'
    assert review['reviewed_by'] == 'FIXTURE-REVIEWER'
    assert review['calculated_current_amount'] == 0
    other = run('synthetic-source-b')
    assert not any(i['manual_override'] for c in other['categories'] for i in c['items'])
