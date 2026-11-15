"""CI helper scripts under .github/scripts (issue #389)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_ci_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up while loading
    spec.loader.exec_module(mod)
    return mod


changes = _load("ci_changes")
attribution = _load("check_attribution")

AGENT_COMMIT = "Fix it\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
HUMAN_COMMIT = "Update README.md"
BLOCK = "Agent-Harness: claude-code\nAgent-Model: claude-opus\nAgent-Host: cloud\n"


# --- changes -----------------------------------------------------------------

@pytest.mark.parametrize("paths", [["README.md"], ["docs/swarms.md", "AGENTS.md"],
                                   [".github/pull_request_template.md"], []])
def test_docs_only_runs_no_heavy_job(paths):
    assert changes.classify(paths) == {"python": False, "dashboard": False, "deploy": False}


def test_python_only_skips_dashboard():
    assert changes.classify(["server/main.py"]) == {"python": True, "dashboard": False, "deploy": False}


def test_dashboard_only_skips_python():
    assert changes.classify(["dashboard/src/App.jsx"]) == {"python": False, "dashboard": True, "deploy": False}


def test_workflow_change_runs_everything():
    assert all(changes.classify([".github/workflows/ci.yml"]).values())
    assert all(changes.classify([".github/scripts/ci_changes.py"]).values())


@pytest.mark.parametrize("path", ["pyproject.toml", "config/default.yaml", "scripts/seed_demo.py",
                                  "cli_agent/client.py", "examples/robot-sidecar/sidecar.py",
                                  "examples/robot-sidecar/tests/test_x.py", "ltms_mcp/server.py",
                                  "docs/vocabulary.jsonld", "deploy/compose/.env.example",
                                  "dashboard/src/lib/swarm-key-vectors.json"])
def test_python_paths(path):
    assert changes.classify([path])["python"]


@pytest.mark.parametrize("path", ["deploy/ltms.service.template", "examples/x/deploy/x.service"])
def test_deploy_paths(path):
    assert changes.classify([path])["deploy"]


def test_contract_vectors_run_both():
    out = changes.classify(["ltms_contract/vectors.json"])
    assert out["python"] and out["dashboard"]


def test_cli_all_and_stdin():
    script = str(SCRIPTS / "ci_changes.py")
    out = subprocess.run([sys.executable, script, "--all"], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["python=true", "dashboard=true", "deploy=true"]
    out = subprocess.run([sys.executable, script], input="README.md\ndashboard/x.js\n",
                         capture_output=True, text=True, check=True).stdout
    assert out.split() == ["python=false", "dashboard=true", "deploy=false"]


# --- attribution -------------------------------------------------------------

def test_full_block_passes():
    assert attribution.check("Closes #1.\n\n" + BLOCK, [], [AGENT_COMMIT]).ok


def test_indented_block_passes():
    body = "Closes #1.\n\n" + "".join(f"    {line}\n" for line in BLOCK.splitlines())
    assert attribution.check(body, ["agent-task"], [AGENT_COMMIT]).ok


def test_harness_none_passes_for_human():
    assert attribution.check("Typo fix.\n\nAgent-Harness: none\n", [], [HUMAN_COMMIT]).ok
    assert attribution.check("Agent-Harness: none\nAgent-Model:\nAgent-Host:\n", [], [HUMAN_COMMIT]).ok


def test_human_pr_without_block_or_agent_trailers_passes():
    assert attribution.check("Fix one word.", [], [HUMAN_COMMIT]).ok
    assert attribution.check("", [], []).ok


def test_agent_pr_without_block_fails_with_paste_text():
    res = attribution.check("Closes #1.", [], [AGENT_COMMIT])
    assert not res.ok
    assert attribution.PASTE_AGENT in res.message and attribution.PASTE_HUMAN in res.message


def test_agent_task_label_without_block_fails():
    assert not attribution.check("Closes #1.", ["bug", "agent-task"], [HUMAN_COMMIT]).ok


@pytest.mark.parametrize("trailer", ["Agent-Model: qwen3:8b", "Co-authored-by: GitHub Copilot <x@y>",
                                     "Claude-Session: https://example.org/s"])
def test_other_agent_trailers_count(trailer):
    assert not attribution.check("", [], [f"msg\n\n{trailer}\n"]).ok


def test_placeholders_fail():
    body = "Agent-Harness: <harness>\nAgent-Model: <model>\nAgent-Host: <host>\n"
    res = attribution.check(body, [], [AGENT_COMMIT])
    assert not res.ok and "Agent-Harness, Agent-Model, Agent-Host" in res.message
    # Without agent trailers or the agent-task label, unfilled placeholders count as a human PR.
    assert attribution.check(body, [], [HUMAN_COMMIT]).ok


def test_partial_block_fails():
    assert not attribution.check("Agent-Harness: claude-code\nAgent-Model: x\n", [], [HUMAN_COMMIT]).ok


def test_harness_none_with_agent_commits_fails():
    res = attribution.check("Agent-Harness: none\n", [], [AGENT_COMMIT])
    assert not res.ok and "none" in res.message


def test_template_passes_for_human():
    template = (SCRIPTS.parent / "pull_request_template.md").read_text(encoding="utf-8")
    assert attribution.check(template, [], [HUMAN_COMMIT]).ok
    assert not attribution.check(template, ["agent-task"], [AGENT_COMMIT]).ok


def test_main_reads_env(tmp_path, monkeypatch, capsys):
    commits = tmp_path / "commits"
    commits.write_text(HUMAN_COMMIT + "\0" + AGENT_COMMIT + "\0", encoding="utf-8")
    monkeypatch.setenv("COMMITS_FILE", str(commits))
    monkeypatch.setenv("LABELS", "")
    monkeypatch.setenv("BODY", "No block")
    assert attribution.main() == 1
    assert "::error" in capsys.readouterr().out
    monkeypatch.setenv("BODY", BLOCK)
    assert attribution.main() == 0
