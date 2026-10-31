"""Authenticated analysis, simulation, imports and auditable case review API."""
import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import Principal, authenticate
from .catalog import Catalog, save_scenario
from .ingestion import ImportRequest, import_csv, persist_artifact
from .models import AnalysisRequest, SimulationRequest, StrictModel
from .simulation import execute_simulation, vendor_status
from .storage import ROOT, ConflictError, execute_case, get_store

load_dotenv(ROOT / '.env', override=False)
app = FastAPI(title='Cooling Advisor', version='0.2.0', description='Evidence-backed cooling decision support; no actuator endpoints.')
bearer = HTTPBearer(auto_error=False)


class BodyLimitMiddleware:
    """Bound actual ASGI body bytes before parsing, including chunked requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope,receive,send)
        chunks=[]
        size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            chunk=message.get('body',b'')
            size+=len(chunk)
            if size>2_500_000:
                response=JSONResponse({'detail':'Request exceeds 2.5 MB'},status_code=413)
                return await response(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body',False):
                break
        body=b''.join(chunks)
        delivered=False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        return await self.app(scope,replay,send)


app.add_middleware(BodyLimitMiddleware)

def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    return authenticate(credentials.credentials if credentials else None)


@lru_cache(maxsize=32)
def repository(owner_id='local-demo-operator', access_token=None):
    return get_store(owner_id, access_token)


def store_for(principal):
    # Do not cache JWT-bearing clients: avoid retaining tokens after sign-out/expiry.
    if principal.token:
        return get_store(principal.owner_id,principal.token)
    return repository(principal.owner_id)


def scoped_catalog(principal):
    return Catalog(owner_id=principal.owner_id,allowed_zones=principal.zones)


_LIMITS = defaultdict(deque)
_LOCK = threading.Lock()
_SIMULATION_SLOT = threading.BoundedSemaphore(1)


def rate_limit(principal, action, limit=12):
    now = time.monotonic()
    key = (principal.owner_id,action)
    with _LOCK:
        while _LIMITS[key] and _LIMITS[key][0] < now-60:
            _LIMITS[key].popleft()
        if len(_LIMITS[key]) >= limit:
            raise HTTPException(429, 'Operation rate exceeded; retry after one minute',headers={'Retry-After':'60'})
        _LIMITS[key].append(now)


def require_scenario(principal, scenario_id):
    try:
        return scoped_catalog(principal).get(scenario_id)
    except ValueError:
        raise HTTPException(422,'Unknown or unauthorized scenario')


@app.get('/health')
def health():
    return {'status':'ok','version':'0.2.0','equipment_commands':False}


@app.get('/settings')
def settings(p: Principal = Depends(authorize)):
    return {'auth_mode':os.getenv('COOLING_AUTH_MODE','local_token'), 'owner_id':p.owner_id,
            'storage':os.getenv('COOLING_STORAGE','sqlite'), 'zones':p.zones,
            'gemini_configured':bool(os.getenv('GEMINI_API_KEY')),
            'supabase_configured':bool(os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_ANON_KEY')),
            'simulator':{k:v for k,v in vendor_status().items() if k != 'path'}}


@app.get('/catalog')
def catalog(p: Principal = Depends(authorize)):
    return scoped_catalog(p).list()


@app.get('/scenarios/{scenario_id}')
def scenario(scenario_id: str, p: Principal = Depends(authorize)):
    return require_scenario(p,scenario_id).model_dump(mode='json')


@app.post('/simulations')
def simulate(request: SimulationRequest, p: Principal = Depends(authorize)):
    rate_limit(p,'simulation',3)
    if 'zone-a' not in p.zones:
        raise HTTPException(403,'Simulation workspace requires zone-a membership')
    if not _SIMULATION_SLOT.acquire(blocking=False):
        raise HTTPException(409,'Another simulation is running; retry when it completes')
    try:
        run = execute_simulation(request, owner_id=p.owner_id)
        save_scenario(run,owner_id=p.owner_id)
        return run.model_dump(mode='json')
    except (TimeoutError, subprocess.TimeoutExpired):
        raise HTTPException(504,'Simulation timed out; no scenario imported')
    except Exception:
        raise HTTPException(503,'Simulation failed or pinned source is unavailable; see server logs/setup')
    finally:
        _SIMULATION_SLOT.release()


@app.post('/imports')
def upload(request: ImportRequest, p: Principal = Depends(authorize)):
    rate_limit(p,'import',6)
    if 'zone-a' not in p.zones:
        raise HTTPException(403,'Import workspace requires zone-a membership')
    try:
        run = import_csv(request,p.owner_id)
        persist_artifact(request,p.owner_id)
        save_scenario(run,owner_id=p.owner_id)
        return run.model_dump(mode='json')
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(422,str(exc)[:500])


@app.post('/analysis')
def analyze(request: AnalysisRequest, idempotency_key: Annotated[str, Header(min_length=1,max_length=120)],
            p: Principal = Depends(authorize)):
    require_scenario(p,request.scenario_id)
    rate_limit(p,'analysis')
    if request.agent_mode == 'gemini' and not os.getenv('GEMINI_API_KEY'):
        raise HTTPException(503,'GEMINI_NOT_CONFIGURED; use rules mode or configure the backend key')
    try:
        return execute_case(request,idempotency_key,store_for(p),catalog=scoped_catalog(p),allowed_zones=p.zones).model_dump(mode='json')
    except ConflictError as exc:
        raise HTTPException(409,str(exc))
    except Exception:
        raise HTTPException(503,'Storage or workflow service unavailable')


@app.get('/cases')
def cases(p: Principal = Depends(authorize)):
    return store_for(p).recent()


@app.get('/cases/{case_id}')
def case(case_id: str, p: Principal = Depends(authorize)):
    try:
        return store_for(p).get(case_id).model_dump(mode='json')
    except KeyError:
        raise HTTPException(404,'Unknown case')
    except ConflictError as exc:
        raise HTTPException(409,str(exc))


@app.get('/cases/{case_id}/reviews')
def reviews(case_id: str, p: Principal = Depends(authorize)):
    case(case_id,p)
    return store_for(p).reviews(case_id)


@app.get('/cases/{case_id}/events')
def events(case_id: str, p: Principal = Depends(authorize)):
    return case(case_id,p)['events']


class Decision(StrictModel):
    action: Literal['acknowledge','mark_for_review','dismiss']


@app.post('/cases/{case_id}/decision')
def decision(case_id: str, payload: Decision, p: Principal = Depends(authorize)):
    case(case_id,p)
    store_for(p).decision(case_id,payload.action)
    return {'recorded':True,'action':payload.action,'equipment_command':False}


@app.get('/monitoring')
def monitoring(p: Principal = Depends(authorize)):
    rows = store_for(p).recent()
    results = []
    for row in rows:
        try:
            results.append(store_for(p).get(row['case_id']))
        except ConflictError:
            pass
    return {'window':'latest 20 cases for the authenticated owner','case_count':len(rows),
            'completed':sum(r.status == 'completed' for r in results),
            'blocked_or_failed':sum(r.status != 'completed' for r in results),
            'model_calls':sum(r.model_calls for r in results), 'model_tokens':sum(r.model_tokens for r in results),
            'tool_calls':sum(r.tool_calls for r in results),
            'latency_ms':[r.elapsed_ms for r in results],
            'guard_events':[e.model_dump() for r in results for e in r.events if e.actor in ('G1','G2','G3') and e.status in ('rejected','blocked')]}
