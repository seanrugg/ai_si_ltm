"""Guard checks for scripts/reset_test_db.py (issue #143). No database needed."""
from __future__ import annotations

import pytest

from scripts.reset_test_db import check_target, main

GOOD = "postgresql+asyncpg://u:p@h/ltms_test"


def test_accepts_a_test_database_with_matching_confirm():
    assert check_target(GOOD, "ltms_test") == "ltms_test"


@pytest.mark.parametrize("url, confirm", [
    ("postgresql+asyncpg://u:p@h/ltms", "ltms"),                      # not *_test
    ("postgresql+asyncpg://u:p@h/ltms_test_prod", "ltms_test_prod"),  # _test not at the end
    (GOOD, "ltms_tests"),                                             # wrong confirm
    (GOOD, ""),                                                       # empty confirm
    ("sqlite+aiosqlite:///./ltms_test", "ltms_test"),                 # not PostgreSQL
    ("postgresql+asyncpg://u:p@h/LTMS_test", "LTMS_test"),            # uppercase not allowed
    ('postgresql+asyncpg://u:p@h/x"_test', 'x"_test'),                # no quoting tricks
])
def test_rejects_unsafe_targets(url, confirm):
    with pytest.raises(ValueError):
        check_target(url, confirm)


def test_main_refuses_without_touching_the_database_or_printing_the_url(monkeypatch, capsys):
    monkeypatch.setenv("LTMS_DATABASE_URL", "postgresql+asyncpg://user:s3cret@h/ltms")
    assert main(["--confirm", "ltms"]) == 2
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err
    assert "refused" in out.err
