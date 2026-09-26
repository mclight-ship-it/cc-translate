"""Frozen pre-extraction history implementation from 0f78865, only a test oracle."""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from cc_storage import atomic_write_json


SOURCE = '''
_HISTORY_LOCK = threading.Lock()

def load_history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log_error("load_history", e)
        return []

def add_history(input_text: str, output_text: str, is_dict: bool, limit: int,
                is_code: bool = False, kind: Optional[str] = None,
                sig: Optional[str] = None) -> None:
    if kind not in ("text", "dict", "code", "ocr"):
        if is_code:
            kind = "code"
        elif is_dict:
            kind = "dict"
        else:
            kind = "text"
    with _HISTORY_LOCK:
        entries = load_history()
        entries.insert(0, {
            "ts": time.strftime("%Y-%m-%d %H:%M"),
            "input": input_text or "",
            "output": output_text or "",
            "is_dict": bool(is_dict),
            "is_code": bool(is_code),
            "kind": kind,
            "sig": sig or "",
        })
        del entries[max(1, int(limit)):]
        try:
            _atomic_write_json(HISTORY_PATH, entries)
        except Exception as e:
            log_error("add_history", e)

def find_cached_translation(text: str, kind: str, sig: str):
    if not text or not text.strip():
        return None
    if kind not in ("text", "dict", "code"):
        return None
    key = text.strip()
    for entry in load_history():
        if (entry.get("kind") == kind
                and (entry.get("sig") or "") == (sig or "")
                and (entry.get("input") or "").strip() == key):
            out = (entry.get("output") or "").strip()
            if out:
                return out
    return None

def clear_history() -> None:
    try:
        if os.path.exists(HISTORY_PATH):
            os.remove(HISTORY_PATH)
    except Exception as e:
        log_error("clear_history", e)
'''


def namespace(path, logger):
    values = dict(globals(), HISTORY_PATH=path, log_error=logger, _atomic_write_json=atomic_write_json)
    exec(compile(SOURCE, "<frozen-history-reference>", "exec"), values)
    return values
