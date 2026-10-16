"""P2 end-to-end: the CLI test agent drives the full pipeline."""
import json

from cli_agent.agent import main


def test_cli_end_to_end(tmp_path, http, admin, capsys):
    key = tmp_path / "keys" / "e2e-agent.json"
    srv = ["--key", str(key), "--server", "http://testserver"]
    assert main(["genkey", "--name", "e2e-agent", "--out", str(key)]) == 0
    assert (key.stat().st_mode & 0o777) == 0o600
    assert main(["genkey", "--name", "e2e-agent", "--out", str(key)]) == 1  # no overwrite
    capsys.readouterr()

    assert main(["enroll", *srv, "--kind", "agent", "--permissions", "contribute,recall"], http=http) == 0
    member_id = json.loads(capsys.readouterr().out)["id"]
    assert main(["send", *srv, "--count", "5"], http=http) == 1  # not approved yet
    admin.post(f"/api/admin/members/{member_id}/approve", json={})

    assert main(["send", *srv, "--count", "20", "--seed", "7"], http=http) == 0
    assert main(["send", *srv, "--count", "5"], http=http) == 0  # resumes from the server's head
    capsys.readouterr()
    assert main(["verify", *srv], http=http) == 0
    assert capsys.readouterr().out.startswith("OK  25 records")

    assert main(["recall", *srv, "--intent", "approach", "--target", "vector"], http=http) == 0
    pack = json.loads(capsys.readouterr().out)
    assert pack["count"] > 0 and pack["outcomes"]

    rep = admin.get("/api/admin/integrity").json()["members"][0]
    assert rep["status"] == "ok" and rep["records"] == 25
    actions = admin.get("/api/admin/timeline", params={"type": "action", "outcome": "reflex_stop"}).json()
    assert all(r["outcome"] == "reflex_stop" for r in actions["records"])
