# AGENTS.md — rules for coding agents in this repo

You are working on **LTMS**, the Long-Term Memory Server for Sean's robot mesh.
It stores signed, hash-chained records from robots and AI agents. Correctness
matters more than speed: a bug here can silently corrupt a robot's memory.

Read this whole file before you change anything. When this file and anything
else disagree, this file wins (except for direct instructions from Sean).

---

## 1. Ground rules

1. **One task, one branch, one pull request.** Never commit to `main`; it is
   protected. Branch name: `agent/<your-name>/<issue-number>-<short-slug>`,
   for example `agent/qwen/12-cli-dry-run`.
2. **Only work on a GitHub issue labeled `agent-task`**, or on something Sean
   asked you for directly. If the issue is unclear, comment on it with your
   question and stop. Do not guess at scope.
3. **Stay inside the files the issue names.** If you think another file must
   change, say so in the PR description and explain why. Do not refactor,
   rename or "tidy" code you were not asked to touch.
4. **Tests must pass before you open a PR.** Run `pytest`. If a test fails and
   you can't fix it within the issue's scope, open the PR as a draft and paste
   the failure.
5. **Never weaken a test to make it pass.** Don't delete it, don't loosen an
   assertion, don't add `skip`. If you believe a test is wrong, say so in the
   PR and leave it as it is.
6. **Never commit secrets or local data:** key files (`keys/`, `*.json` key
   files), `.env`, `*.db`, virtualenvs, `__pycache__`.
7. **Do not merge your own PR.** Sean merges.
8. **If you get stuck, stop and report.** A clear "I could not do X because Y"
   beats a large speculative change.

## 2. Do-not-touch list

| Path | Why | What to do instead |
|---|---|---|
| `CONTRACT.md`, `ltms_contract/canonical.py`, `ltms_contract/record.py`, `ltms_contract/vectors.json`, `scripts/make_vectors.py` | The frozen record contract. Changing one byte breaks every record ever signed. CI fails any PR that touches these. | Open an issue describing the need. |
| `config/schema.sql` | Generated from `server/models/`. | Change the models, then run `python -m scripts.dump_schema`. |
| `.github/workflows/` | CI is the safety net. | Ask in the issue. |
| `deploy/`, `PLESK_DEPLOYMENT_PLAN.md` | Runs on the production server as root or as the site user. | Only when an issue explicitly says so. |
| `server/services/ingest.py`, `server/services/integrity.py` | Chain verification. | Only when an issue explicitly says so. |
| `AGENTS.md` | These rules. | Suggest changes in your PR description. |

## 3. Setup and commands

```bash
git checkout main && git pull origin main
git checkout -b agent/<you>/<issue>-<slug>
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                    # must pass: about 80 tests, about 30 s
```

Also useful:

```bash
python -m scripts.dump_schema             # after any change in server/models/
python -m server.manage show-config
LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db LTMS_ADMIN_PASSWORD=dev-password-123 \
  LTMS_COOKIE_SECURE=false uvicorn server.main:app --port 8080
```

Dashboard work (`dashboard/`): `cd dashboard && npm ci && npm test && npm run build`.
For a backend with demo data, see `dashboard/README.md` (`python -m scripts.seed_demo`).

## 4. How the code is organized

| Area | Where | Notes |
|---|---|---|
| Record contract | `ltms_contract/` | Hashing, signing, verification. Shared by every member. **Frozen.** |
| HTTP routes | `server/api/public.py`, `member.py`, `admin.py` | Thin: parse the request, call a service, return JSON. |
| Business logic | `server/services/` | All database work lives here. |
| Database tables | `server/models/` | SQLAlchemy 2.0. The source of truth for the schema. |
| Request plumbing | `server/api/deps.py` | `transaction(request)` gives you a session that commits for you. |
| Reference client | `cli_agent/client.py` | How a member talks to the server. |
| Tests | `server/tests/` | Fixtures are in `conftest.py`: `http`, `admin`, `mesh`, `db_exec`. |
| Dashboard | `dashboard/src/` | React pages in `pages/`, shared pieces in `components/ui.jsx`. See `dashboard/README.md`. |

Patterns to copy:
- A new admin endpoint: copy one in `server/api/admin.py`. It takes `admin: AdminAuth = ADMIN`, opens `async with transaction(request) as session:` and returns a dict.
- Errors: `raise ServiceError(status, "snake_code", "Human message")`. Never return error dicts by hand.
- A new test: copy the shape of `server/tests/test_recall.py`. Use `mesh.member("name")` to get an enrolled, approved member client.
- Records sent by tests: `client.append(type, summary=..., payload={...})`, then `client.flush()`.

## 5. Pull request checklist

Your PR description must include:

```
Closes #<issue>

## What changed
- <file>: <one line>

## How I tested
- pytest: <N passed, M skipped>      ← paste the real last line
- <any manual check>

## Anything I was unsure about
- <or "none">
```

Before opening the PR:
- [ ] `pytest` passes, and the result is pasted above
- [ ] For dashboard changes: `npm test && npm run build` pass in `dashboard/`, and you include a screenshot if you can
- [ ] `git diff main --stat` shows only the files the issue expected
- [ ] No secrets, key files, `.db` or `.venv` in `git status`
- [ ] If you changed `server/models/`, you ran `python -m scripts.dump_schema` and committed `config/schema.sql`
- [ ] New behavior has a test

## 6. Vocabulary

- **member**: a robot or agent with an Ed25519 key, enrolled by the admin.
- **record**: one signed memory entry. It has a **header** (permanent), a **payload** (may be purged later) and a **seal** (`hash`, `sig`).
- **chain**: each member's records link through `prev_hash`. A gap or edit is visible on the Integrity page.
- **context pack**: what a robot asks for before acting: recent outcomes, communications, lessons and policies.
