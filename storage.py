"""Persistent state & diff utilities for Kingtrans tracker.

- Stores per-tracking_no state in JSON files (atomic writes)
- Computes diffs between previous snapshot and current fetch
- Designed to be push-agnostic: return a DiffResult for higher layers (e.g., Telegram)

Usage:
    from storage import JsonStateStore, diff_result_pretty
    from kingtrans_client import KingtransClient

    client = KingtransClient()
    store = JsonStateStore(base_dir="state")
    result = client.query("1ZW1008Y6816279460")
    diff = store.update_with_result("1ZW1008Y6816279460", result)
    print(diff_result_pretty(diff))
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import json
import os
import tempfile
import time

# Import only for typing; no runtime dependency on client internals
try:
    from kingtrans_client import TrackItem, TrackSummary, TrackResult
except Exception:  # pragma: no cover - allow standalone typing if module path differs
    TrackItem = object  # type: ignore
    TrackSummary = object  # type: ignore
    TrackResult = object  # type: ignore


# =============================
# Models
# =============================

@dataclass
class StateSnapshot:
    """Minimal persisted state for diffing.
    - item_keys: stable keys of known track items
    - summary_fp: fingerprint of summary for quick change detection
    - updated_ts: unix seconds when snapshot saved
    """
    item_keys: List[str]
    summary_fp: str
    updated_ts: float


@dataclass
class DiffResult:
    tracking_no: str
    added_keys: List[str]            # keys newly seen (stable keys)
    summary_changed: bool            # summary fingerprint change
    before_fp: str                   # previous summary fp (may be "")
    after_fp: str                    # new summary fp
    # Optional enrichments (filled if caller provides items)
    added_items: Optional[List[dict]] = None  # list of {index,sdate,place,intro}
    summary_before: Optional[dict] = None     # TrackSummary as dict
    summary_after: Optional[dict] = None      # TrackSummary as dict


# =============================
# Helpers
# =============================

def _item_key(it: TrackItem) -> str:
    """Return a stable key for a TrackItem.

    Provider indexes are positional and can shift when new events are inserted,
    so prefer the event content for persistence/diffing.
    """
    try:
        sdate = getattr(it, "sdate", "") or ""
        place = getattr(it, "place", "") or ""
        intro = getattr(it, "intro", "") or ""
        event_key = f"{sdate}|{place}|{intro}"
        if event_key != "||":
            return event_key
        idx = getattr(it, "index", "") or ""
        if idx:
            return idx
        return event_key
    except Exception:
        return str(it)


def _provider_index_key(it: TrackItem) -> str:
    try:
        return getattr(it, "index", "") or ""
    except Exception:
        return ""


def fingerprint_summary(summary: TrackSummary) -> str:
    """Create a stable SHA-256 fingerprint of critical summary fields."""
    try:
        parts = [
            getattr(summary, "latest_time", ""),
            getattr(summary, "latest_place", ""),
            getattr(summary, "latest_intro", ""),
            getattr(summary, "status_code", ""),
        ]
        raw = "|".join(parts)
    except Exception:
        raw = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)  # atomic on POSIX, best-effort on Windows


# =============================
# Store interface & JSON impl
# =============================

class StateStore:
    """Abstract interface for state persistence."""
    def load(self, tracking_no: str) -> Optional[StateSnapshot]:
        raise NotImplementedError

    def save(self, tracking_no: str, snap: StateSnapshot) -> None:
        raise NotImplementedError

    def update_with_result(self, tracking_no: str, result: TrackResult, save: bool = True) -> DiffResult:
        """Compute diff vs existing state and persist new snapshot."""
        # Gather current keys
        items = getattr(result, "items", []) or []
        item_keys = [_item_key(it) for it in items]
        new_fp = fingerprint_summary(getattr(result, "summary"))

        # Load old
        old = self.load(tracking_no)
        before_fp = old.summary_fp if old else ""
        old_keys = set(old.item_keys) if old else set()
        old_uses_provider_indexes = bool(old_keys) and all("|" not in k for k in old_keys)

        added_keys = []
        for key, item in zip(item_keys, items):
            if key in old_keys:
                continue
            if old_uses_provider_indexes and _provider_index_key(item) in old_keys:
                continue
            added_keys.append(key)
        summary_changed = (before_fp != new_fp)

        # Save new snapshot if requested. Callers that need an external side
        # effect (e.g. Telegram) can delay this until the side effect succeeds.
        snap = StateSnapshot(item_keys=item_keys, summary_fp=new_fp, updated_ts=time.time())
        if save:
            self.save(tracking_no, snap)

        # Enrich diff with readable content
        key_to_item: Dict[str, dict] = {}
        for it in items:
            key_to_item[_item_key(it)] = {
                "index": getattr(it, "index", ""),
                "sdate": getattr(it, "sdate", ""),
                "place": getattr(it, "place", ""),
                "intro": getattr(it, "intro", ""),
            }

        added_items = [key_to_item[k] for k in added_keys if k in key_to_item]
        summary_before = asdict(getattr(result, "summary")) if hasattr(result, "summary") else None
        summary_after = summary_before  # same structure; we don't keep old content here

        return DiffResult(
            tracking_no=tracking_no,
            added_keys=added_keys,
            summary_changed=summary_changed,
            before_fp=before_fp,
            after_fp=new_fp,
            added_items=added_items,
            summary_before=summary_before,
            summary_after=summary_after,
        )


class JsonStateStore(StateStore):
    """File-system JSON store. One file per tracking_no under base_dir.
    Safe for simple single-process schedulers.
    """
    def __init__(self, base_dir: str = "state") -> None:
        self.base = Path(base_dir)

    def _path(self, tracking_no: str) -> Path:
        safe = "".join(c for c in tracking_no if c.isalnum() or c in ("-", "_"))
        return self.base / f"{safe}.json"

    def load(self, tracking_no: str) -> Optional[StateSnapshot]:
        p = self._path(tracking_no)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return StateSnapshot(
                item_keys=list(data.get("item_keys", [])),
                summary_fp=str(data.get("summary_fp", "")),
                updated_ts=float(data.get("updated_ts", 0.0)),
            )
        except Exception:
            # Corrupt file: rename for inspection
            corrupt = p.with_suffix(".corrupt.json")
            try:
                p.rename(corrupt)
            except Exception:
                pass
            return None

    def save(self, tracking_no: str, snap: StateSnapshot) -> None:
        p = self._path(tracking_no)
        payload = {
            "item_keys": snap.item_keys,
            "summary_fp": snap.summary_fp,
            "updated_ts": snap.updated_ts,
            "version": 1,
        }
        _atomic_write_text(p, json.dumps(payload, ensure_ascii=False, indent=2))


# =============================
# Utilities for presentation / export
# =============================

def diff_result_pretty(diff: DiffResult) -> str:
    lines = [
        f"Tracking: {diff.tracking_no}",
        f"Summary changed: {diff.summary_changed}",
        f"Added items: {len(diff.added_keys)}",
    ]
    if diff.added_items:
        for it in diff.added_items:
            lines.append(f"  + {it['sdate']} | {it['place']} | {it['intro']}")
    return "\n".join(lines)


def export_items_to_csv(items: List[dict], path: str) -> None:
    """Export TrackItems (dict form) to CSV."""
    import csv
    headers = ["index", "sdate", "place", "intro"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for it in items:
            w.writerow({k: it.get(k, "") for k in headers})


if __name__ == "__main__":
    # Small demo (requires kingtrans_client available and network)
    try:
        from kingtrans_client import KingtransClient
        client = KingtransClient()
        store = JsonStateStore()
        tn = "1ZW1008Y6816279460"
        res = client.query(tn)
        diff = store.update_with_result(tn, res)
        print(diff_result_pretty(diff))
    except Exception as e:
        print("Demo failed:", e)
