"""Mind settings: environment variables (or an env file passed with ``--env``).

Every setting has a neutral default; nothing site-specific (AGENTS.md rule 10).
See ``docs/mind.md`` for the full list.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Agenda categories, in their fixed order (the epic, #338). The weight of each is
# the most tasks of that kind one wake may take; 0 turns the category off.
CATEGORIES = ("obligations", "verify", "predictions", "reflect", "study")
DEFAULT_WEIGHTS = {"obligations": 3, "verify": 2, "predictions": 2, "reflect": 1, "study": 1}


class ConfigError(ValueError):
    pass


def parse_duration(text: str) -> float:
    """``90``, ``90s``, ``30m``, ``4h``, ``1d`` → seconds."""
    t = (text or "").strip().lower()
    if not t:
        raise ConfigError("empty duration")
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(t[-1])
    num = t[:-1] if unit else t
    try:
        value = float(num) * (unit or 1)
    except ValueError:
        raise ConfigError(f"bad duration {text!r}: use e.g. 30m, 4h") from None
    if value < 0:
        raise ConfigError(f"bad duration {text!r}: negative")
    return value


def parse_weights(text: str | None) -> dict[str, int]:
    """``obligations=3,verify=2`` → weights; unnamed categories keep their default."""
    out = dict(DEFAULT_WEIGHTS)
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        if name not in CATEGORIES:
            raise ConfigError(f"unknown agenda category {name!r}: one of {', '.join(CATEGORIES)}")
        try:
            out[name] = max(0, int(val))
        except ValueError:
            raise ConfigError(f"bad weight for {name}: {val!r}") from None
    return out


@dataclass(frozen=True)
class DreamCondition:
    """When the deep tasks may run (#339 comment): ``never``, ``always``,
    ``window HH:MM-HH:MM`` (local time, plus ``day_dreams`` outside it),
    or ``idle N`` (minutes without a decision or action, or charging)."""
    mode: str = "never"
    start_min: int = 0
    end_min: int = 0
    idle_min: int = 30

    @classmethod
    def parse(cls, text: str | None) -> "DreamCondition":
        t = (text or "never").strip().lower()
        if t in ("never", "always"):
            return cls(t)
        if t.startswith("window"):
            rng = t[len("window"):].strip()
            try:
                a, b = rng.split("-")
                return cls("window", _hhmm(a), _hhmm(b))
            except (ValueError, ConfigError):
                raise ConfigError(f"bad MIND_DREAM {text!r}: use window 01:00-05:00") from None
        if t.startswith("idle"):
            rest = t[len("idle"):].strip() or "30"
            try:
                return cls("idle", idle_min=int(rest))
            except ValueError:
                raise ConfigError(f"bad MIND_DREAM {text!r}: use idle 30 (minutes)") from None
        raise ConfigError(f"bad MIND_DREAM {text!r}: never, always, window HH:MM-HH:MM or idle N")

    def in_window(self, minute_of_day: int) -> bool:
        if self.start_min <= self.end_min:
            return self.start_min <= minute_of_day < self.end_min
        return minute_of_day >= self.start_min or minute_of_day < self.end_min  # crosses midnight


def _hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ConfigError(f"bad time {s!r}")
    return h * 60 + m


@dataclass(frozen=True)
class MindConfig:
    server: str
    key_path: str
    state_path: str
    brain: str = "none"
    model_family: str | None = None
    max_tasks: int = 5
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    brain_calls: int = 10
    tokens: int = 20000
    seconds: float = 300.0
    daily_writes: int = 20
    novelty: float = 0.92
    corpus: int = 200
    embedder: str = "auto"
    dream: DreamCondition = DreamCondition()
    day_dreams: int = 1
    lookback_days: int = 7
    study_query: str = ""
    health: bool = True
    paused: bool = False
    place: str | None = None
    # The Claude Code brain (#341)
    claude_bin: str = "claude"
    claude_model: str | None = None
    claude_timeout: float = 180.0
    claude_max_usd: float | None = None
    # The local brain (#340): any OpenAI-compatible model server
    local_url: str | None = None
    local_model: str | None = None
    local_timeout: float = 120.0
    local_max_tokens: int = 1024
    local_api_key: str | None = field(default=None, repr=False)

    @property
    def outbox_path(self) -> str:
        return self.state_path + ".outbox"

    @property
    def intro_path(self) -> str:
        return self.state_path + ".introduced"

    @property
    def brain_workdir(self) -> str:
        """An empty working directory for a model brain's process (#341)."""
        return self.state_path + ".workdir"


def _on(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "on", "yes", "true")


def read_env_file(path: str) -> dict:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    return out


# Model families by name prefix (#521). Longer names first, so "codellama" isn't "llama".
FAMILIES = (
    ("codellama", "llama"), ("llama", "llama"), ("qwen", "qwen"), ("qwq", "qwen"), ("gemma", "gemma"),
    ("mixtral", "mistral"), ("mistral", "mistral"), ("ministral", "mistral"), ("devstral", "mistral"),
    ("codestral", "mistral"), ("magistral", "mistral"), ("phi", "phi"), ("glm", "glm"),
    ("nemotron", "nemotron"), ("deepseek", "deepseek"), ("granite", "granite"), ("olmo", "olmo"),
    ("command-r", "cohere"), ("aya", "cohere"), ("gpt-oss", "gpt-oss"), ("kimi", "kimi"),
    ("minimax", "minimax"), ("smollm", "smollm"),
)
# Not listed: fine-tunes named for their tuner ("hermes3", "dolphin"), whose family is the
# base model's. Those resolve from the base name if it appears ("dolphin-llama3"), else None.


def family_of(model: str | None) -> str | None:
    """A model's family from its name, for cross-family review: ``gemma4:31b`` -> ``gemma``,
    ``hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M`` -> ``qwen``. None if it isn't recognised;
    set ``MIND_MODEL_FAMILY`` then."""
    if not model:
        return None
    base = str(model).strip().lower().split(":")[0].rsplit("/", 1)[-1]
    for prefix, family in FAMILIES:
        if base.startswith(prefix):
            return family
    words = [w for w in re.split(r"[-_.\s]+", base) if w]
    for prefix, family in FAMILIES:  # e.g. "dolphin-llama3": the base family is a later word
        if any(w.startswith(prefix) for w in words[1:]):
            return family
    return None


def load(env: dict | None = None) -> MindConfig:
    e = dict(os.environ if env is None else env)
    server = e.get("LTMS_SERVER", "").strip()
    key_path = e.get("LTMS_KEY_PATH", "").strip()
    if not server:
        raise ConfigError("LTMS_SERVER is required (e.g. https://ltms.example.org)")
    if not key_path:
        raise ConfigError("LTMS_KEY_PATH is required: the member's key file")
    state = e.get("MIND_STATE_PATH", "").strip() or os.path.splitext(key_path)[0] + ".mind.json"
    brain = e.get("MIND_BRAIN", "none").strip().lower() or "none"
    if brain == "ollama":  # the local brain covers Ollama (#340)
        brain = "local"
    family = (e.get("MIND_MODEL_FAMILY", "").strip().lower()
              or ("claude" if brain == "claude-code" else None)
              or (family_of(e.get("MIND_LOCAL_MODEL")) if brain == "local" else None))
    max_usd = e.get("MIND_CLAUDE_MAX_USD", "").strip()
    try:
        return MindConfig(
            server=server.rstrip("/"),
            key_path=key_path,
            state_path=state,
            brain=brain,
            model_family=family,
            max_tasks=max(0, int(e.get("MIND_MAX_TASKS", "5") or 5)),
            weights=parse_weights(e.get("MIND_WEIGHTS")),
            brain_calls=max(0, int(e.get("MIND_BRAIN_CALLS", "10") or 10)),
            tokens=max(0, int(e.get("MIND_TOKENS", "20000") or 20000)),
            seconds=parse_duration(e.get("MIND_SECONDS", "300") or "300"),
            daily_writes=max(0, int(e.get("MIND_DAILY_WRITES", "20") or 20)),
            novelty=float(e.get("MIND_NOVELTY", "0.92") or 0.92),
            corpus=max(1, int(e.get("MIND_CORPUS", "200") or 200)),
            embedder=e.get("MIND_EMBEDDER", "auto").strip().lower() or "auto",
            dream=DreamCondition.parse(e.get("MIND_DREAM")),
            day_dreams=max(0, int(e.get("MIND_DAY_DREAMS", "1") or 1)),
            lookback_days=max(1, int(e.get("MIND_LOOKBACK_DAYS", "7") or 7)),
            study_query=e.get("MIND_STUDY_QUERY", "").strip(),
            health=_on(e.get("MIND_HEALTH"), True),
            paused=_on(e.get("MIND_PAUSED"), False),
            place=e.get("MIND_PLACE", "").strip() or None,
            claude_bin=e.get("MIND_CLAUDE_BIN", "").strip() or "claude",
            claude_model=e.get("MIND_CLAUDE_MODEL", "").strip() or None,
            claude_timeout=parse_duration(e.get("MIND_CLAUDE_TIMEOUT", "180") or "180"),
            claude_max_usd=float(max_usd) if max_usd else None,
            local_url=e.get("MIND_LOCAL_URL", "").strip() or None,
            local_model=e.get("MIND_LOCAL_MODEL", "").strip() or None,
            local_timeout=parse_duration(e.get("MIND_LOCAL_TIMEOUT", "2m") or "2m"),
            local_max_tokens=max(64, int(e.get("MIND_LOCAL_MAX_TOKENS", "1024") or 1024)),
            local_api_key=e.get("MIND_LOCAL_API_KEY", "").strip() or None,
        )
    except ValueError as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(str(exc)) from None
