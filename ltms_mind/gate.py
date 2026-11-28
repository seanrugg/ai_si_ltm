"""The gate: a proposal is written only if it passes every rule (#338).

1. **shape**: a known kind, one the task allows (``ALLOWED_KINDS``; else ``wrong_kind``,
   #540), with the fields that kind needs.
2. **purpose**: it does something (answers, reviews, predicts, checks, asks, or
   teaches a lesson). A summary alone has no kind and fails here.
3. **evidence**: ``refs`` to records or dataset ``sources``. Questions are exempt.
   Every ``refs`` value must be a record key, ``<agent>:<seq>`` (or a list of
   them); anything else, like a prompt's placeholder copied back, is ``bad_ref`` (#525).
4. **novelty**: not a near-duplicate (cosine >= threshold) of my or my swarm's
   recent records, or of something already accepted this wake.
5. **budget**: the daily write cap (checked by the loop with ``Budget``).

Each rejection has a reason code, counted in the cycle event, and a ``detail``
saying what failed (``Gate.detail`` after ``check``), listed there too (#526).
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

from ltms_mind.brains.base import ALLOWED_KINDS, CHECK_OUTCOMES, KINDS, REPORT_STATUSES, VERDICTS, Proposal

log = logging.getLogger("ltms_mind.gate")

# Reason codes (docs/mind.md).
BAD_PROPOSAL = "bad_proposal"
NO_PURPOSE = "no_purpose"
WRONG_KIND = "wrong_kind"
NO_EVIDENCE = "no_evidence"
BAD_REF = "bad_ref"
DUPLICATE = "duplicate"
OVER_BUDGET = "over_budget"
REASONS = (NO_PURPOSE, WRONG_KIND, BAD_PROPOSAL, BAD_REF, NO_EVIDENCE, DUPLICATE, OVER_BUDGET)

# A record key: the contract's member name, a colon, a sequence number from 1.
REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}:[1-9][0-9]*$")

MAX_SUMMARY = 500


# ---------------------------------------------------------------------------- embedders
class LexicalEmbedder:
    """Character-trigram counts. No dependencies; used when fastembed isn't installed
    and in tests. Coarser than a model: it catches rewordings that share most text."""

    name = "lexical"

    def embed(self, texts: list[str]) -> list[dict]:
        out = []
        for t in texts:
            s = " " + re.sub(r"\s+", " ", (t or "").lower()).strip() + " "
            out.append(Counter(s[i:i + 3] for i in range(max(0, len(s) - 2))))
        return out

    @staticmethod
    def cosine(a: dict, b: dict) -> float:
        if not a or not b:
            return 0.0
        dot = sum(v * b.get(k, 0) for k, v in a.items())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0


class FastEmbedder:
    """fastembed with BAAI/bge-small-en-v1.5 (docs/embedding-benchmark.md)."""

    name = "fastembed"
    MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # optional dependency
        self._model = TextEmbedding(model_name=self.MODEL)

    def embed(self, texts: list[str]) -> list:
        return [list(v) for v in self._model.embed(list(texts))]

    @staticmethod
    def cosine(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0


def make_embedder(choice: str = "auto"):
    if choice == "lexical":
        return LexicalEmbedder()
    try:
        return FastEmbedder()
    except Exception as exc:  # noqa: BLE001 - not installed, or the model can't be loaded
        if choice == "fastembed":
            raise
        log.warning("fastembed unavailable (%s); novelty uses the lexical fallback", exc)
        return LexicalEmbedder()


# ---------------------------------------------------------------------------- the gate
def shape_error(p: Proposal) -> str | None:
    """None if the proposal has what its kind needs; otherwise what's wrong."""
    if not isinstance(p.summary, str) or not p.summary.strip():
        return "empty summary"
    if len(p.summary) > MAX_SUMMARY:
        return f"summary over {MAX_SUMMARY} characters"
    if p.kind == "answer" and not (p.refs or {}).get("answers"):
        return "an answer needs refs.answers"
    if p.kind == "review":
        if not (p.refs or {}).get("reviews"):
            return "a review needs refs.reviews"
        if (p.payload or {}).get("verdict") not in VERDICTS:
            return f"a review needs payload.verdict: {', '.join(VERDICTS)}"
        if not (p.payload or {}).get("reasons"):
            return "a review needs payload.reasons"
    if p.kind == "prediction":
        pl = p.payload or {}
        if not pl.get("claim") or not pl.get("measure") or not pl.get("check_after"):
            return "a prediction needs payload.claim, measure and check_after"
    if p.kind == "prediction-check":
        if not (p.refs or {}).get("checks"):
            return "a prediction check needs refs.checks"
        if (p.payload or {}).get("outcome") not in CHECK_OUTCOMES:
            return f"a prediction check needs payload.outcome: {', '.join(CHECK_OUTCOMES)}"
    if p.kind == "question" and not p.peers:
        return "a question needs peers: who it is put to"
    if p.kind == "report":
        if not (p.refs or {}).get("acts_on") or not (p.payload or {}).get("directive"):
            return "a report needs refs.acts_on and payload.directive"
        if (p.payload or {}).get("status") not in REPORT_STATUSES:
            return f"a report needs payload.status: {', '.join(REPORT_STATUSES)}"
        if p.payload["status"] == "couldnt" and not p.payload.get("reason"):
            return "a 'couldnt' report needs payload.reason"
    if len(p.sources or []) > 16:
        return "at most 16 sources"
    return None


def ref_error(refs) -> str | None:
    """None if every ``refs`` value is a record key (``<agent>:<seq>``) or a list of them."""
    if refs is None:
        return None
    if not isinstance(refs, dict):
        return "refs must be an object"
    for k, v in refs.items():
        values = v if isinstance(v, list) else [v]
        if not values:
            return f"refs.{k} is empty"
        for x in values:
            if not isinstance(x, str) or not REF_RE.match(x):
                return f"refs.{k} must be <agent>:<seq>, got {str(x)[:40]!r}"
    return None


class Gate:
    def __init__(self, embedder, threshold: float, corpus: list[str]) -> None:
        self.embedder, self.threshold = embedder, threshold
        self.detail: str | None = None  # what the last check() refused, in words (#526)
        texts = [t for t in corpus if t]
        self._vecs = embedder.embed(texts) if texts else []

    def check(self, p: Proposal, task_kind: str | None = None) -> str | None:
        """None if it passes (and remembers it for novelty); otherwise the reason code,
        with what failed in ``self.detail``. With ``task_kind``, the proposal's kind must
        be one that task allows."""
        reason, self.detail = self._check(p, task_kind)
        return reason

    def _check(self, p: Proposal, task_kind: str | None = None) -> tuple[str | None, str | None]:
        if p.kind not in KINDS:
            return NO_PURPOSE, f"unknown kind {str(p.kind)[:40]!r}"
        allowed = ALLOWED_KINDS.get(task_kind) if task_kind else None
        if allowed is not None and p.kind not in allowed:
            return WRONG_KIND, f"{task_kind} allows {', '.join(allowed)}; got {p.kind}"
        err = shape_error(p)
        if err:
            return BAD_PROPOSAL, err
        err = ref_error(p.refs)
        if err:
            return BAD_REF, err
        if p.kind != "question" and not (p.refs or p.sources):
            return NO_EVIDENCE, "no refs or sources"
        vec = self.embedder.embed([p.summary])[0]
        best = max((self.embedder.cosine(vec, v) for v in self._vecs), default=0.0)
        if best >= self.threshold:
            return DUPLICATE, f"near-copy of a recent record (similarity {best:.2f} >= {self.threshold})"
        self._vecs.append(vec)
        return None, None
