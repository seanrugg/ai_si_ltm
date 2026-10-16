# LTMS — Long-Term Memory Server

Append-only, signed, tamper-evident memory for an AI/robot mesh (Z3-R0, Vector,
Jetson, cloud agents). Members sign every record with Ed25519 and chain it to
their previous one; the server verifies, stores, and serves it back for recall.

* **Design:** `Robot-Mesh-and-Long-Term-Memory-Plan.md`
* **Implementation plan & status:** `LTMS_ARCHITECTURE_PLAN.md`
* **Record contract (frozen v1):** `CONTRACT.md`, vectors in `ltms_contract/vectors.json`

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                   # ~30 s on SQLite

# Run a dev server on SQLite
LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db LTMS_ADMIN_PASSWORD=change-me \
LTMS_COOKIE_SECURE=false uvicorn server.main:app --port 8080
```

Walk the whole pipeline with the CLI test agent:

```bash
python -m cli_agent genkey --name test-agent-1 --out keys/test-agent-1.json
python -m cli_agent enroll --key keys/test-agent-1.json --server http://localhost:8080 \
    --kind agent --permissions contribute,recall

# approve it (dashboard comes in P3; until then, curl):
curl -c /tmp/cj -X POST localhost:8080/api/auth/admin/login -H 'content-type: application/json' \
    -d '{"username":"admin","password":"change-me"}'
curl -b /tmp/cj localhost:8080/api/admin/members/pending
curl -b /tmp/cj -X POST localhost:8080/api/admin/members/<id>/approve \
    -H 'content-type: application/json' -H 'x-ltms-admin: 1' -d '{}'

python -m cli_agent send   --key keys/test-agent-1.json --server http://localhost:8080 --count 20
python -m cli_agent verify --key keys/test-agent-1.json --server http://localhost:8080
python -m cli_agent recall --key keys/test-agent-1.json --server http://localhost:8080 \
    --intent approach --target vector --place living-room
```

## PostgreSQL

```bash
createdb ltms
psql -v ON_ERROR_STOP=1 -d ltms -f config/schema.sql      # generated from the models
LTMS_DB_PASSWORD=... LTMS_ADMIN_PASSWORD=... uvicorn server.main:app --port 8080
# tests against Postgres (each test gets a throwaway database):
LTMS_TEST_DATABASE_URL=postgresql+asyncpg://ltms@localhost/postgres pytest
```

Run **one** worker in production (rate limits are in-process).

## Writing a member

`cli_agent/client.py` is the reference client — copy it for Z3-R0's relay, a
Vector adapter or a cloud agent:

```python
from cli_agent.client import LTMSClient
c = LTMSClient("https://ltms.example", key_path="keys/z3-r0.json")
c.login()                       # challenge/response; also returns the chain head
c.append("action", summary="Approach Vector", place="living-room", peers=["vector"],
         outcome="reflex_stop", payload={"distance_m": 0.8, "speed_mps": 0.15})
c.flush()                       # retries are safe: already-stored records come back "duplicate"
pack = c.context(intent="approach", target="vector", place="living-room")
```

Never commit key files; `keys/` is git-ignored.
