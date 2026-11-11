"""MCP dataset tools (issue #241): plain functions over an LTMSClient."""
from __future__ import annotations

import pytest

from ltms_mcp import MAX_ITEM_CHARS, ToolError, list_datasets, read_item, search_datasets
from server.tests.test_datasets_read import _dataset, _reads, data_dir  # noqa: F401


@pytest.fixture
def agent(mesh):
    a = mesh.member("agent-x", kind="agent")
    a.login()
    return a


def test_list_datasets(agent, admin):
    did = _dataset(admin)
    [ds] = list_datasets(agent)
    assert ds["id"] == did and ds["name"] == "Manuals" and ds["current_version"] == 1


def test_search_by_name_or_id(agent, admin):
    did = _dataset(admin)
    by_name = search_datasets(agent, "manuals", "wheel stalls")
    by_id = search_datasets(agent, did, "wheel stalls")
    assert [h["key"] for h in by_name] == [h["key"] for h in by_id] == ["drive.md"]


def test_unknown_dataset(agent, admin):
    _dataset(admin)
    with pytest.raises(ToolError) as err:
        search_datasets(agent, "nope", "x")
    assert err.value.code == "not_found"


def test_read_item_is_logged_and_citable(agent, admin, db_exec):
    did = _dataset(admin)
    cycle = "5b0f6a3e-2c1d-4e8f-9a7b-1c2d3e4f5a6b"
    item = read_item(agent, "Manuals", "drive.md", cycle=cycle)
    assert item["version"] == 1 and len(item["sha256"]) == 64 and "wheel" in item["text"].lower()
    assert item["cite_as"] == {"dataset": did, "version": 1, "item": "drive.md"}
    assert "note" not in item
    assert [(k, str(c)) for k, _, c in _reads(db_exec)] == [("drive.md", cycle)]
    with pytest.raises(ToolError) as err:
        read_item(agent, did, "missing.md")
    assert err.value.code == "not_found"


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, []

    def datasets(self):
        return [{"id": "d1", "name": "Big"}]

    def read_item(self, dataset_id, key, *, version=None, cycle=None):
        self.calls.append((dataset_id, key, version, cycle))
        return {"key": key, "version": 2, "media_type": "text/plain", "sha256": "f" * 64, "text": self.text}


def test_long_text_is_truncated_with_a_note():
    fake = FakeClient("x" * (MAX_ITEM_CHARS + 5))
    item = read_item(fake, "big", "huge.txt", version=2)
    assert fake.calls == [("d1", "huge.txt", 2, None)]
    assert len(item["text"]) == MAX_ITEM_CHARS
    assert "truncated" in item["note"] and "20,005" in item["note"]
