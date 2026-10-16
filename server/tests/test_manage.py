"""server.manage admin CLI."""
import io
import json

from cli_agent.client import LTMSClient
from ltms_contract import generate_key
from server.config import load_config
from server.manage import main


def test_manage_commands(tmp_path, monkeypatch, capsys):
    cfg = load_config(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db", enable_scheduler=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert main(["set-admin-password", "--username", "boss", "--password-stdin"], config=cfg) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO("a much longer password\n"))
    assert main(["set-admin-password", "--username", "boss", "--password-stdin"], config=cfg) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("another long password\n"))
    assert main(["set-admin-password", "--username", "boss", "--password-stdin"], config=cfg) == 0
    assert "updated" in capsys.readouterr().out

    # Enroll a member through the real app, approve it from the CLI.
    from fastapi.testclient import TestClient
    from server.main import create_app

    app = create_app(cfg.model_copy(update={"cookie_secure": False}))
    with TestClient(app) as http:
        c = LTMSClient(name="z3-r0", signing_key=generate_key(), http=http)
        c.enroll("robot", ["contribute", "recall"])
        assert main(["approve", "z3-r0", "--permissions", "contribute"], config=cfg) == 0
        assert "approved z3-r0" in capsys.readouterr().out
        c.append("event", summary="hello")
        c.flush()
        r = http.post("/api/auth/admin/login", json={"username": "boss", "password": "another long password"})
        assert r.status_code == 200

    assert main(["list-members"], config=cfg) == 0
    assert "z3-r0" in capsys.readouterr().out
    assert main(["integrity"], config=cfg) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "ok"
    assert main(["show-config"], config=cfg) == 0
    assert "admin_password" not in capsys.readouterr().out
