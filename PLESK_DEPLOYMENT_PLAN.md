# LTMS — Plesk Deployment Plan

Deploys LTMS to the IONOS Plesk server as a subdomain of `cucorn.com`.
This plan uses **`ltms.cucorn.com`**; if you choose another name, substitute it everywhere.

**How it fits together**

```
Robots / agents / your browser
        │ HTTPS
        ▼
Plesk nginx  (ltms.cucorn.com, Let's Encrypt)
        │ proxies everything to 127.0.0.1:8090
        ▼
LTMS server  (uvicorn, 1 worker, systemd service "ltms")
   ├─ /api/*        the API
   └─ /             the dashboard (built files in dashboard/dist)
        │
        ▼
PostgreSQL  (Plesk-managed database, localhost:5432)
```

| | Where it lives |
|---|---|
| Code | `/var/www/vhosts/cucorn.com/ltms-app`, deployed by **Plesk Git** (outside the web root) |
| Python environment | `ltms-app/.venv` (built by the deploy script) |
| Secrets | `/etc/ltms/ltms.env`, readable only by root and the site's system user |
| Service | `systemd`: `ltms.service`, plus `ltms-restart.path`, which restarts it after each deploy |
| Backups | `/var/backups/ltms`, nightly at 02:30, 14 days kept |

**What you do once, and what you do every time**

| Once (≈ 45 min, root needed for one step) | Every update (≈ 2 min, no root) |
|---|---|
| Steps 1–8 below | Merge the PR on GitHub → Plesk → **Git → Pull now / Deploy** → the log ends with "LTMS is up." |

---

## Before you start: three checks

As root over SSH (your usual top-level login):

```bash
python3 --version                 # need 3.11 or newer
ls /opt/plesk/node/               # Plesk's Node.js versions; need 18+ for the dashboard build
psql --version                    # PostgreSQL client, used for backups
```

* **Python older than 3.11** (Ubuntu 22.04 ships 3.10): `apt install python3.11 python3.11-venv`
  (AlmaLinux/Rocky: `dnf install python3.11`). The deploy script picks the newest Python it finds.
* **No `/opt/plesk/node`**: Plesk → **Tools & Settings → Updates → Add/Remove Components → Node.js support**.
* **No `psql`**: install the PostgreSQL server component the same way (**PostgreSQL server**). It brings the client tools with it.

---

## Step 1 — Create the subdomain

Plesk → **Websites & Domains → cucorn.com → Add Subdomain**: name `ltms`. Leave the document root at its default. Nothing is served from it.

On the **cucorn.com** subscription: **Hosting & DNS → Hosting Settings → SSH access** = `/bin/bash`.
Plesk runs the deploy script with this shell. A chrooted shell can't see Python or Node.

## Step 2 — Create the database

Plesk → **ltms.cucorn.com → Databases → Add Database**

* Type: **PostgreSQL**, server: local
* Name: `ltms`, user: `ltms`, password: generate one and keep it for Step 4

Plesk may add a prefix to both names. Use exactly what it shows.

## Step 3 — Connect Plesk Git (first deploy)

Plesk → **ltms.cucorn.com → Git → Add Repository**

| Field | Value |
|---|---|
| Repository URL | `https://github.com/seanrugg/ai_si_ltm.git` (public, so no key needed) |
| Branch | `main` |
| Deployment mode | **Manual**. You decide when production changes; GitHub merges don't deploy on their own |
| Deployment path | `/ltms-app`. Relative to the subscription, so it ends up at `/var/www/vhosts/cucorn.com/ltms-app`, **not** in `httpdocs` |

Then **Deployment settings → Enable additional deployment actions** and enter exactly:

```
bash deploy/plesk-deploy.sh
```

Click **Deploy now**. The first run takes a few minutes (it builds the Python environment and the dashboard). Its log should end with:

```
dashboard built with node v2x.x
NOTE: restart watcher not installed yet. First deployment? Run deploy/root-setup.sh as root once.
```

## Step 4 — One-time root setup

As root:

```bash
bash /var/www/vhosts/cucorn.com/ltms-app/deploy/root-setup.sh
nano /etc/ltms/ltms.env
```

`root-setup.sh` works out the folder and the site's system user on its own. It installs the service, the restart watcher, the nightly backup, and a template for `/etc/ltms/ltms.env`. In that file, fill in:

* `LTMS_DB_NAME`, `LTMS_DB_USER`, `LTMS_DB_PASSWORD`: from Step 2
* `LTMS_ADMIN_PASSWORD`: your dashboard password (16+ characters)
* `LTMS_DASHBOARD_DIR`: already correct if your path matches `/var/www/vhosts/cucorn.com/ltms-app`

Start it and check:

```bash
systemctl restart ltms
curl -s http://127.0.0.1:8090/api/health      # → {"ok":true,"version":"0.2.0"}
journalctl -u ltms -n 30                       # if not: the error is here
```

On first start the server creates its tables, including the append-only triggers, and the admin account.

## Step 5 — nginx

Plesk → **ltms.cucorn.com → Apache & nginx Settings**

1. **Proxy mode: Off.** Otherwise Plesk rejects the `location /` block as a duplicate, as it did on the LLM project.
2. **Additional nginx directives:** paste the contents of `deploy/nginx-directives.conf`:

```nginx
client_max_body_size 2m;

add_header Strict-Transport-Security "max-age=31536000" always;

location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
}
```

`X-Forwarded-For` is set to the real address on purpose, rather than appended to. A client can't fake its IP to slip past the per-IP rate limits.

## Step 6 — HTTPS

Plesk → **ltms.cucorn.com → SSL/TLS Certificates → Install (Let's Encrypt)**, with auto-renew on.
Then **Hosting Settings → Permanent SEO-safe 301 redirect from HTTP to HTTPS**.

The admin cookie is `Secure`, so the dashboard login only works over HTTPS. That's intended.

## Step 7 — First login, then remove the bootstrap password

Open `https://ltms.cucorn.com`, sign in. Then, as root:

```bash
nano /etc/ltms/ltms.env          # delete the LTMS_ADMIN_PASSWORD line
systemctl restart ltms
```

The account stays. The line is only read when no admin exists yet. To change the password later:

```bash
bash /var/www/vhosts/cucorn.com/ltms-app/deploy/manage.sh set-admin-password
```

## Step 8 — Smoke test (from the Mac)

```bash
cd ~/qwen/ai_si_ltm && . .venv/bin/activate
python -m scripts.smoke_test https://ltms.cucorn.com --admin-user admin
```

It's read-only and safe on production. It creates nothing. It checks HTTPS and the redirect, the security headers, that every API path refuses requests without credentials, that the dashboard is served, the admin cookie flags, the CSRF guard, and chain integrity. **Every check should pass.** This is the P6 "done when".

Don't send test records to production. Records are permanent by design. The first member to enroll should be a real one, such as Z3-R0's relay or a named agent.

---

## Every update

1. Merge the PR on GitHub (Squash and merge).
2. Plesk → **ltms.cucorn.com → Git → Pull now**, then **Deploy now**.
3. Read the deploy log. The last line should be **`LTMS is up.`**

The deploy script reinstalls Python packages, rebuilds the dashboard, and asks systemd to restart the server. It then waits up to 20 s for the health check. If the log ends with `ERROR: LTMS did not come back`, see Troubleshooting.

**Rolling back:** on GitHub, open the merged PR → **Revert** → merge the revert PR → deploy again. Because every PR is a single squashed commit, a revert undoes exactly that change.

**Database changes:** there is no migration tool yet. Until there is, a PR that changes `server/models/` must say how to update an existing database, and must not be deployed without that step. See issue "Database migrations".

## Backups

Nightly at 02:30 (`/etc/cron.d/ltms-backup` → `deploy/backup.sh`):

* `pg_dump` in custom format to `/var/backups/ltms/ltms-YYYYMMDD-HHMM.dump`, mode 600
* The script reads each dump back (`pg_restore --list`), so a broken dump fails loudly
* 14 days kept; log in `/var/log/ltms-backup.log`

Run one now to confirm it works: `bash /var/www/vhosts/cucorn.com/ltms-app/deploy/backup.sh`

**Keep a copy off the server.** If the VPS is lost, the robots' memory is lost with it. Weekly, from the Mac:

```bash
scp root@cucorn.com:'/var/backups/ltms/ltms-*.dump' ~/Backups/ltms/
```

**Restore test (do it once, then every few months):**

```bash
# create an empty database "ltms_restore" in Plesk first, owned by the same user
pg_restore --no-owner -h localhost -U ltms -d ltms_restore /var/backups/ltms/<file>.dump
LTMS_DATABASE_URL=postgresql+asyncpg://ltms:<password>@localhost/ltms_restore \
  /var/www/vhosts/cucorn.com/ltms-app/.venv/bin/python -m server.manage integrity
```

Every chain should report `ok`. Records are signed, so a restored copy can prove it matches the original. This was tested end to end before release: 39 records backed up, restored into a fresh database, all 4 chains verified.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser shows **502 Bad Gateway** | Service is down | `systemctl status ltms`, `journalctl -u ltms -n 80` |
| Log: `password authentication failed` | Wrong DB name/user/password in `/etc/ltms/ltms.env` (Plesk prefixes) | Copy the exact values from Plesk → Databases, then `systemctl restart ltms` |
| Deploy log: `no Python 3.11+ found` | Old system Python | See "Before you start" |
| Deploy log: `npm not found` | Plesk Node.js support missing | See "Before you start" |
| Deploy log: `restart watcher not installed` after first setup | `root-setup.sh` not run, or failed | Run it again; it's safe to repeat |
| Plesk: "duplicate location /" | Proxy mode still on | Step 5.1 |
| Login form says the password is wrong, and it isn't | You're on `http://` and the Secure cookie was dropped | Use `https://`; check the redirect (Step 6) |
| Everything **429 Too Many Requests** | Rate limit hit, often a misbehaving member | `journalctl -u ltms` shows which path; limits are in `config/default.yaml` |
| Integrity page shows a **broken** chain | Someone edited the database directly | Don't "fix" rows. Restore from the last good backup and investigate |

## Security notes

* The app listens on `127.0.0.1:8090` only. nginx is the only way in, and there are no public pages or API docs.
* `/etc/ltms/ltms.env` is outside the web space, mode 640, root and the site user only.
* The service runs as the site's own system user. systemd makes the filesystem read-only for it and blocks privilege escalation.
* The database user owns its tables, so it could drop the append-only triggers. Anyone with database credentials could edit history. The Integrity page and `manage.sh integrity` detect that, which is why backups and the off-server copy matter.
* **One worker only.** Rate limits live in process memory.
* **Later (P8):** vector search needs the `pgvector` extension, which requires a PostgreSQL superuser to install. Check it's available on the server before starting P8.

## Files

| File | Purpose |
|---|---|
| `deploy/plesk-deploy.sh` | Plesk Git "additional deployment action": venv, pip install, dashboard build, restart, health check |
| `deploy/root-setup.sh` | One-time root setup: secrets file, systemd units, backup cron |
| `deploy/ltms.service.template` | The service (filled in by `root-setup.sh`) |
| `deploy/ltms-restart.path.template`, `deploy/ltms-restart.service` | Restart the service when a deploy touches `tmp/restart.txt` |
| `deploy/ltms.env.example` | Template for `/etc/ltms/ltms.env` |
| `deploy/nginx-directives.conf` | Paste into Plesk |
| `deploy/backup.sh` | Nightly dump + verify + prune |
| `deploy/manage.sh` | Run `server.manage` commands as the right user with the right settings |
| `scripts/smoke_test.py` | Read-only production check |
