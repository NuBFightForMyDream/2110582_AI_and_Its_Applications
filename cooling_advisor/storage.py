"""Owner-scoped case persistence with idempotency and review audit records."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from .models import AnalysisRequest, AnalysisResult

ROOT = Path(__file__).resolve().parent.parent
OWNER = "local-demo-operator"


class ConflictError(Exception):
    pass


def case_summary(row):
    payload = row['result']
    if isinstance(payload,str):
        payload = json.loads(payload)
    payload = payload or {}
    return {'case_id':row['id'],'created_at':row['created_at'],
            'status':payload.get('status','in_progress_or_interrupted'),
            'scenario_id':payload.get('scenario_id'), 'source_kind':payload.get('source_kind'),
            'decision':payload.get('decision')}


def request_hash(request):
    return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()


class SQLiteStore:
    def __init__(self, path=None, owner_id=OWNER):
        self.owner_id = owner_id
        self.path = Path(path or os.getenv("COOLING_SQLITE_PATH", str(ROOT / ".local" / "cooling-advisor.sqlite3")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    result TEXT, UNIQUE(owner_id, dedupe_key)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    case_id TEXT NOT NULL, action TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(case_id, action)
                );
            """)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def reserve(self, request, key):
        digest = request_hash(request)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cases WHERE owner_id=? AND dedupe_key=?", (self.owner_id, key)).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise ConflictError("Idempotency key already belongs to a different request")
                if row["result"] is None:
                    raise ConflictError("Case is in progress or was interrupted; use a new submission key")
                return row["id"], AnalysisResult.model_validate_json(row["result"])
            case_id = str(uuid4())
            con.execute("INSERT INTO cases(id,owner_id,dedupe_key,request_hash) VALUES(?,?,?,?)", (case_id, self.owner_id, key, digest))
            return case_id, None

    def finish(self, result):
        with self.connect() as con:
            cursor = con.execute("UPDATE cases SET result=? WHERE id=? AND owner_id=? AND result IS NULL", (result.model_dump_json(), result.case_id, self.owner_id))
            if cursor.rowcount != 1:
                raise ConflictError("Case is absent, unauthorized or already finalized")

    def get(self, case_id):
        with self.connect() as con:
            row = con.execute("SELECT result FROM cases WHERE id=? AND owner_id=?", (case_id, self.owner_id)).fetchone()
        if not row:
            raise KeyError(case_id)
        if row["result"] is None:
            raise ConflictError("Case is in progress or interrupted")
        return AnalysisResult.model_validate_json(row["result"])

    def recent(self):
        with self.connect() as con:
            rows = con.execute("SELECT id,created_at,result FROM cases WHERE owner_id=? ORDER BY rowid DESC LIMIT 20", (self.owner_id,)).fetchall()
        return [case_summary(r) for r in rows]

    def decision(self, case_id, action):
        if action not in ("acknowledge", "mark_for_review", "dismiss"):
            raise ValueError("Unsupported review action")
        self.get(case_id)
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO decisions(case_id,action) VALUES(?,?)", (case_id, action))

    def reviews(self, case_id):
        self.get(case_id)
        with self.connect() as con:
            rows = con.execute("SELECT action,created_at FROM decisions WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()
        return [dict(row) for row in rows]


class SupabaseStore:
    def __init__(self, owner_id, access_token):
        self.owner_id = owner_id
        from supabase import ClientOptions, create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_ANON_KEY")
        if not url or not key or not access_token:
            raise RuntimeError("Configure Supabase URL and anon/publishable key; sign in with an authorized user")
        self.client = create_client(url, key, options=ClientOptions(postgrest_client_timeout=10, headers={"Authorization": "Bearer " + access_token}))

    def reserve(self, request, key):
        digest = request_hash(request)
        case_id = str(uuid4())
        try:
            self.client.table("cooling_cases").insert({"id": case_id, "owner_id": self.owner_id, "dedupe_key": key, "request_hash": digest}).execute()
            return case_id, None
        except Exception:
            rows = self.client.table("cooling_cases").select("*").eq("owner_id", self.owner_id).eq("dedupe_key", key).execute().data
            if not rows:
                raise
            row = rows[0]
            if row["request_hash"] != digest:
                raise ConflictError("Idempotency key belongs to a different request")
            if row.get("result") is None:
                raise ConflictError("Case is in progress or interrupted")
            return row["id"], AnalysisResult.model_validate(row["result"])

    def finish(self, result):
        response = self.client.table("cooling_cases").update({"result": result.model_dump(mode="json")}).eq("id", result.case_id).eq("owner_id", self.owner_id).is_("result","null").execute()
        if not response.data:
            raise ConflictError("Case is absent, unauthorized or already finalized")

    def get(self, case_id):
        rows = self.client.table("cooling_cases").select("result").eq("id", case_id).eq("owner_id", self.owner_id).execute().data
        if not rows:
            raise KeyError(case_id)
        if rows[0]["result"] is None:
            raise ConflictError("Case is in progress or interrupted")
        return AnalysisResult.model_validate(rows[0]["result"])

    def recent(self):
        rows = self.client.table("cooling_cases").select("id,created_at,result").eq("owner_id", self.owner_id).order("created_at", desc=True).limit(20).execute().data
        return [case_summary(r) for r in rows]

    def decision(self, case_id, action):
        if action not in ("acknowledge", "mark_for_review", "dismiss"):
            raise ValueError("Unsupported review action")
        self.get(case_id)
        self.client.table("cooling_decisions").upsert({"case_id": case_id, "owner_id": self.owner_id, "action": action}, on_conflict="case_id,action").execute()

    def reviews(self, case_id):
        self.get(case_id)
        return self.client.table("cooling_decisions").select("action,created_at").eq("case_id",case_id).eq("owner_id",self.owner_id).order("created_at").execute().data


def get_store(owner_id=OWNER, access_token=None):
    if os.getenv("COOLING_STORAGE", "sqlite") == "supabase":
        return SupabaseStore(owner_id, access_token)
    return SQLiteStore(owner_id=owner_id)


def execute_case(request: AnalysisRequest, key: str, store, **engine_options):
    from .engine import run_analysis
    case_id, existing = store.reserve(request, key)
    if existing:
        return existing
    result = run_analysis(request, case_id=case_id, **engine_options)
    store.finish(result)  # Do not report stored success if persistence fails.
    return result
