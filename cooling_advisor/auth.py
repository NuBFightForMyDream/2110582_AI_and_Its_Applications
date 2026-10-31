"""Request authentication. Local token mode or verified Supabase user JWTs."""
from dataclasses import dataclass
import os
import secrets

from fastapi import HTTPException


@dataclass(frozen=True)
class Principal:
    owner_id: str
    zones: tuple[str, ...]
    token: str | None = None


def supabase_client(token=None):
    from supabase import ClientOptions, create_client
    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_ANON_KEY')
    if not url or not key:
        raise RuntimeError('Supabase URL and anon/publishable key are not configured')
    return create_client(url, key, options=ClientOptions(postgrest_client_timeout=10,
                         headers={'Authorization':'Bearer '+token} if token else {}))


def authenticate(token):
    if os.getenv('COOLING_AUTH_MODE','local_token') == 'supabase':
        if not token:
            raise HTTPException(401, 'Sign in to continue')
        try:
            client = supabase_client(token)
            response = client.auth.get_user(token)
            user_id = str(response.user.id)
            rows = client.table('cooling_memberships').select('zone_id').eq('user_id',user_id).execute().data
        except Exception:
            raise HTTPException(401, 'Session could not be verified')
        zones = tuple(row['zone_id'] for row in rows)
        if not zones:
            raise HTTPException(403, 'No approved data-center zone membership')
        return Principal(user_id,zones,token)
    expected = os.getenv('COOLING_API_TOKEN','')
    if not expected:
        raise HTTPException(503, 'Local API token is not configured; start with python -m cooling_advisor')
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, 'Invalid bearer token')
    return Principal('local-demo-operator',('zone-a',))
