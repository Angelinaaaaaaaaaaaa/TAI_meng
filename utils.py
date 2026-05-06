"""Cross-cutting infrastructure: console I/O, debug logs, path helpers, LLM gateway.

Three concerns live here because none has any domain logic and all are tiny:
- Console/encoding + JSON load + per-task debug-log directory (ContextVar-bound).
- Pure path/string utilities used everywhere.
- Thin OpenAI structured-completion wrapper (`LLMGateway`).
"""

import contextvars
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openai import OpenAI

from models import MiscRefinementResponse

log = logging.getLogger(__name__)


# =============================================================================
# Logging configuration helper (called by CLI main)
# =============================================================================

class _LevelFormatter(logging.Formatter):
    """Plain message for INFO/DEBUG; prefix WARNING/ERROR with the level name.

    Preserves traceback/exception output (``log.exception`` and ``exc_info=True``).
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            msg = f"{record.levelname}: {msg}"
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            msg = f"{msg}\n{self.formatStack(record.stack_info)}"
        return msg


def configure_cli_logging(level: int = logging.INFO) -> None:
    """Set up a single root StreamHandler with a clean format. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level)
    # Drop any pre-existing handlers we previously installed (avoid duplicates
    # when this is called more than once, e.g. from tests).
    for h in list(root.handlers):
        if getattr(h, "_rearrange_managed", False):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(_LevelFormatter())
    handler._rearrange_managed = True  # type: ignore[attr-defined]
    root.addHandler(handler)


# =============================================================================
# Console encoding + safe printing
# =============================================================================

def _configure_stdout() -> None:
    """Prefer UTF-8 for console output when supported."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_configure_stdout()


def _console_safe(text: str) -> str:
    """Return a string safe for the active console encoding."""
    message = str(text)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        message.encode(encoding)
        return message
    except Exception:
        try:
            return message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        except Exception:
            return message.encode("utf-8", errors="replace").decode("utf-8")


def _safe_print(message: str) -> None:
    """Emit a message at INFO level, sanitized for the active console encoding.

    Kept as a thin compatibility wrapper around the logger so existing call sites
    (which previously used this in lieu of a logger) keep working.
    """
    log.info(_console_safe(message))


# =============================================================================
# JSON I/O + per-task debug log dir
# =============================================================================

def load_json_file(file_path) -> Dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


_PIPELINE_LOG_DIR: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "pipeline_log_dir", default=None
)


def set_pipeline_log_dir(log_dir: Optional[str]) -> contextvars.Token[Optional[str]]:
    """Bind ``log_dir`` for the current task; return token for ``reset_pipeline_log_dir``."""
    return _PIPELINE_LOG_DIR.set(log_dir)


def reset_pipeline_log_dir(token: contextvars.Token[Optional[str]]) -> None:
    """Restore previous log dir binding."""
    _PIPELINE_LOG_DIR.reset(token)


def save_debug_log(
    data: Any,
    step_name: str,
    base_dir=None,
    *,
    log_dir=None,
) -> str:
    """Save an intermediate checkpoint to ``<log_dir>/<step_name>.json``.

    Resolution order: explicit ``log_dir`` > ContextVar binding > ``<base_dir>/logs``.
    """
    effective_log = log_dir or _PIPELINE_LOG_DIR.get()
    if effective_log:
        resolved_log_dir = Path(effective_log)
    else:
        base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        resolved_log_dir = base / "logs"

    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    file_path = resolved_log_dir / f"{step_name}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log.debug("Saved %s log to: %s", step_name, file_path)
    return str(file_path)


# =============================================================================
# Path / string utilities
# =============================================================================

def _normalize_path(path: str) -> str:
    return path.strip().rstrip("/")


def _is_under_path(node_path: str, root_path: str) -> bool:
    node = _normalize_path(node_path)
    root = _normalize_path(root_path)
    if not node or not root:
        return False
    if node == root:
        return True
    return node.startswith(root + "/")


def _chunked(items: List[Dict], size: int) -> Iterable[List[Dict]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _detect_course_prefix(paths: List[str]) -> str:
    """Detect the common course prefix from database paths (e.g. 'CS 61A/', 'EECS 106B/')."""
    if not paths:
        return ""
    top_segments = [p.split("/")[0] for p in paths if "/" in p]
    if not top_segments:
        return ""
    most_common = Counter(top_segments).most_common(1)[0]
    if most_common[1] > len(paths) * 0.5:
        return most_common[0] + "/"
    return ""


def _derive_course_name(
    db_filename: Optional[str] = None, input_filename: Optional[str] = None
) -> str:
    """Derive a course identifier from the db or input filename for folder organization."""
    if db_filename:
        name = Path(db_filename).name
        name = re.sub(r"_metadata\.db$", "", name)
        return name.strip().replace(" ", "_")
    if input_filename:
        name = Path(input_filename).name
        name = re.sub(r"^bfs_v3_tree_?", "", name)
        name = re.sub(r"\.json$", "", name)
        if name:
            return name.strip().replace(" ", "_")
    return "default"


# =============================================================================
# LLM gateway
# =============================================================================

# Pinned to match Step 1 (`bfs_v4.py`, `classify_v4.py`) — using the unversioned
# alias would let OpenAI swap underlying weights mid-experiment and break the
# determinism we get from `seed=DEFAULT_LLM_SEED`.
DEFAULT_LLM_MODEL = "gpt-5-mini-2025-08-07"
DEFAULT_LLM_SEED = 42


def _llm_parse(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_payload: object,
    response_model,
    seed: int = DEFAULT_LLM_SEED,
):
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, indent=2)},
        ],
        response_format=response_model,
        seed=seed,
    )
    return completion.choices[0].message.parsed


class LLMGateway:
    """Thin wrapper around structured OpenAI completions (single place for parse calls)."""

    def __init__(self, client: Optional[OpenAI] = None) -> None:
        self._client = client or OpenAI()

    @property
    def client(self) -> OpenAI:
        return self._client

    def parse_structured(
        self,
        model: str = DEFAULT_LLM_MODEL,
        system_prompt: str = "",
        user_payload: object = None,
        response_model=None,
        seed: int = DEFAULT_LLM_SEED,
    ):
        return _llm_parse(
            self._client, model, system_prompt, user_payload, response_model, seed=seed
        )

    def refine_miscellaneous_groups(
        self, misc_payload: List[Dict[str, str]], seed: int = DEFAULT_LLM_SEED
    ) -> MiscRefinementResponse:
        completion = self._client.beta.chat.completions.parse(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are organizing course files. These files were originally dumped into a 'Lecture Miscellaneous' folder. "
                        "Your job is to further categorize them into specific logical groups (e.g., 'Review', 'Tutorials', 'Administrivia', 'Project Files', etc.).\n"
                        "Group items by their overarching subject or file type.\n"
                        "Provide a new group name and a brief description for each item.\n"
                        "Don't create more than 5 new groups. If items are very diverse, it's okay to keep some in Miscellaneous, but try to find logical clusters where possible.\n"
                    ),
                },
                {"role": "user", "content": json.dumps(misc_payload, indent=2)},
            ],
            response_format=MiscRefinementResponse,
            seed=seed,
        )
        return completion.choices[0].message.parsed
