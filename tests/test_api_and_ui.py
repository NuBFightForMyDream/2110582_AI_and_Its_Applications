from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from cooling_advisor.api import app, repository, _LIMITS, authorize
from cooling_advisor.auth import Principal


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('COOLING_API_TOKEN','test-token-not-for-deployment')
    monkeypatch.setenv('COOLING_AUTH_MODE','local_token')
    monkeypatch.setenv('COOLING_STORAGE','sqlite')
    monkeypatch.setenv('COOLING_SQLITE_PATH',str(tmp_path/'api.db'))
    monkeypatch.setenv('COOLING_CATALOG_DIR',str(tmp_path/'catalog'))
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    repository.cache_clear()
    _LIMITS.clear()
    app.dependency_overrides.clear()
    with TestClient(app) as instance:
        yield instance
    app.dependency_overrides.clear()
    repository.cache_clear()


HEADERS={'Authorization':'Bearer test-token-not-for-deployment','Idempotency-Key':'test-one'}
CSV='timestamp,interval_hours,cooling_kw,it_kw,max_inlet_c,setpoint_c\n2026-01-01T00:00:00Z,0.25,40,100,24,18\n'


def test_api_authentication_and_case_workflow(client):
    assert client.get('/health').status_code==200
    assert client.get('/catalog').status_code==401
    assert client.get('/catalog',headers=HEADERS).status_code==200
    response=client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady'})
    assert response.status_code==200
    payload=response.json()
    assert payload['status']=='completed'
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady'}).json()['case_id']==payload['case_id']
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'peak'}).status_code==409
    assert client.get(f"/cases/{payload['case_id']}/events",headers=HEADERS).json()
    assert client.post(f"/cases/{payload['case_id']}/decision",headers=HEADERS,json={'action':'mark_for_review'}).json()['equipment_command'] is False
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'unknown'}).status_code==422
    assert client.get('/monitoring',headers=HEADERS).json()['completed']==1


def test_import_roundtrip_is_private_and_analysis_only(client):
    response=client.post('/imports',headers=HEADERS,json={'label':'Imported','csv_text':CSV})
    assert response.status_code==200,response.text
    sid=response.json()['scenario_id']
    assert client.get('/scenarios/'+sid,headers=HEADERS).json()['comparison_enabled'] is False
    result=client.post('/analysis',headers=HEADERS,json={'scenario_id':sid,'mode':'analyze'}).json()
    assert result['baseline']['total_kwh']==35
    case_id=result['case_id']
    app.dependency_overrides[authorize]=lambda:Principal('other-user',('zone-a',))
    assert client.get('/scenarios/'+sid,headers=HEADERS).status_code==422
    assert client.get('/cases/'+case_id,headers=HEADERS).status_code==404


def test_api_rejects_bad_import_overlarge_request_and_unconfigured_agent(client):
    assert client.post('/imports',headers=HEADERS,json={'label':'bad','csv_text':'a,b\n1,2'}).status_code==422
    assert client.post('/imports',headers={**HEADERS,'Content-Type':'application/json'},content='x'*2_500_001).status_code==413
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady','agent_mode':'gemini'}).status_code==503
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady','max_inlet_c':100}).status_code==422


def test_rate_limit_is_enforced_before_more_analysis(client):
    for i in range(12):
        response=client.post('/analysis',headers={**HEADERS,'Idempotency-Key':f'case-{i}'},json={'scenario_id':'steady'})
        assert response.status_code==200
    assert client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady'}).status_code==429


def test_dashboard_recommends_a_setpoint_for_each_simulated_situation(client,monkeypatch):
    from streamlit.testing.v1 import AppTest
    def request(method,url,**kwargs):
        kwargs.pop('timeout',None)
        return client.request(method,urlsplit(url).path,**kwargs)
    monkeypatch.setattr('cooling_advisor.gateway.httpx.request',request)
    path=Path(__file__).parents[1]/'cooling_advisor/dashboard.py'
    ui=AppTest.from_file(str(path),default_timeout=30).run()
    assert not ui.exception
    situations=ui.selectbox[0].options
    assert len(situations)>=2
    assert not any('Steady load' in label for label in situations)  # Test fixtures stay out of the picker.
    for scenario_id in [r['scenario_id'] for r in client.get('/catalog',headers=HEADERS).json() if r['source_kind']=='simulated']:
        ui.selectbox[0].set_value(scenario_id).run()
        assert not ui.exception,scenario_id
        result=ui.session_state['results'][scenario_id]
        assert result['status']=='completed'
        assert any('RECOMMENDED SETPOINT' in m.value for m in ui.markdown)
    calls=len(ui.session_state['results'])
    ui.selectbox[0].set_value(scenario_id).run()
    assert len(ui.session_state['results'])==calls  # Revisiting a situation does not create another case.


def test_supabase_auth_uses_verified_user_and_approved_membership(monkeypatch):
    from types import SimpleNamespace
    from cooling_advisor.auth import authenticate
    from fastapi import HTTPException
    class FakeClient:
        def __init__(self):
            self.auth=self
            self.filters=[]
        def get_user(self,token):
            assert token=='valid-test-jwt'
            return SimpleNamespace(user=SimpleNamespace(id='verified-user'))
        def table(self,name):
            assert name=='cooling_memberships'
            return self
        def select(self,*_):return self
        def eq(self,*args):self.filters.append(args);return self
        def execute(self):return SimpleNamespace(data=[{'zone_id':'zone-a'}])
    client=FakeClient()
    monkeypatch.setenv('COOLING_AUTH_MODE','supabase')
    monkeypatch.setattr('cooling_advisor.auth.supabase_client',lambda token:client)
    principal=authenticate('valid-test-jwt')
    assert principal.owner_id=='verified-user'
    assert principal.zones==('zone-a',)
    assert client.filters==[('user_id','verified-user')]
    with pytest.raises(HTTPException) as exc:
        authenticate(None)
    assert exc.value.status_code==401


def test_review_records_survive_repeated_writes(client):
    result=client.post('/analysis',headers=HEADERS,json={'scenario_id':'steady'}).json()
    path='/cases/'+result['case_id']
    for _ in range(2):
        assert client.post(path+'/decision',headers=HEADERS,json={'action':'mark_for_review'}).status_code==200
    reviews=client.get(path+'/reviews',headers=HEADERS).json()
    assert len(reviews)==1
    assert reviews[0]['action']=='mark_for_review'


def test_chunked_body_limit_cannot_be_bypassed_without_content_length(client):
    response=client.post('/imports',headers={**HEADERS,'Content-Type':'application/json'},
                         content=(b'x'*500_001 for _ in range(5)))
    assert response.status_code==413
