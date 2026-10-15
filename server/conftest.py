"""Pytest bootstrap: put the repo root on ``sys.path`` so ``import server.*`` works.

The server package lives at ``server/``; its parent (``ai_si_ltm/``) must be on
``sys.path`` for the absolute ``server.<subpackage>`` imports used throughout the
codebase. This file is picked up by pytest automatically and also by any tool
that imports ``conftest``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
