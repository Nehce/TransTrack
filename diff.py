"""Pure diff utilities (no persistence) for Kingtrans tracker.

Use this when you already have two snapshots in memory and want to compute
what changed without touching the filesystem.

Inputs are plain dicts/lists, so this module does not depend on kingtrans_client.

Example:
    from diff import compute_diff, pretty_print_diff

    old_summary = {...}
    new_summary = {...}
    old_items = [{"index": "trackitem_0_1", "sdate": "...", "place": "...", "intro": "..."}, ...]
    new_items = [...]

    d = compute_diff(old_summary, new_summary, old_items, new_items)
    print(pretty_print_diff(d))
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple
import hashlib


# =============================
# Helpers
# =============================

def item_key(item: Dict) -> str:
    """Stable key for a track item based on event content.

    Provider indexes are positional and can shift when new events are inserted,
    so they are only a fallback for otherwise-empty records.
    """
    sdate = str(item.get("sdate", "")).strip()
    place = str(item.get("place", "")).strip()
    intro = str(item.get("intro", "")).strip()
    event_key = f"{sdate}|{place}|{intro}"
    if event_key != "||":
        return event_key
    idx = str(item.get("index", "")).strip()
    if idx:
        return idx
    return event_key


def fingerprint_summary(summary: Dict) -> str:
    """SHA-256 fingerprint from critical summary fields.
    Accepts plain dict like TrackSummary.__dict__.
    """
    parts = [
        str(summary.get("latest_time", "")),
        str(summary.get("latest_place", "")),
        str(summary.get("latest_intro", "")),
        str(summary.get("status_code", "")),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =============================
# Diff models
# =============================

@dataclass
class DiffLite:
    added: List[Dict]
    removed: List[Dict]
    unchanged: List[Dict]
    summary_changed: bool
    before_fp: str
    after_fp: str


# =============================
# Core diff
# =============================

def compute_diff(
    old_summary: Dict,
    new_summary: Dict,
    old_items: Iterable[Dict],
    new_items: Iterable[Dict],
) -> DiffLite:
    """Compute item-level and summary-level diffs (pure, no IO)."""
    # Build key maps
    old_map: Dict[str, Dict] = {item_key(it): it for it in old_items}
    new_map: Dict[str, Dict] = {item_key(it): it for it in new_items}

    # Set operations
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys = [k for k in new_items and [item_key(it) for it in new_items] if k not in old_keys]
    removed_keys = list(old_keys - new_keys)
    unchanged_keys = list(old_keys & new_keys)

    added = [new_map[k] for k in added_keys]
    removed = [old_map[k] for k in removed_keys]
    unchanged = [new_map[k] for k in unchanged_keys]

    # Summary fingerprint
    before_fp = fingerprint_summary(old_summary or {})
    after_fp = fingerprint_summary(new_summary or {})

    return DiffLite(
        added=added,
        removed=removed,
        unchanged=unchanged,
        summary_changed=(before_fp != after_fp),
        before_fp=before_fp,
        after_fp=after_fp,
    )


# =============================
# Formatting helpers
# =============================

def pretty_print_diff(d: DiffLite) -> str:
    lines = [
        f"Summary changed: {d.summary_changed}",
        f"Added: {len(d.added)} | Removed: {len(d.removed)} | Unchanged: {len(d.unchanged)}",
    ]
    for it in d.added:
        lines.append(f"  + {it.get('sdate','')} | {it.get('place','')} | {it.get('intro','')}")
    for it in d.removed:
        lines.append(f"  - {it.get('sdate','')} | {it.get('place','')} | {it.get('intro','')}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Tiny self-check
    old_items = [
        {"index": "trackitem_0_1", "sdate": "2025-08-16 06:00:00", "place": "Obertraubling, DE", "intro": "Processing"},
        {"index": "trackitem_0_2", "sdate": "2025-08-16 04:30:00", "place": "Obertraubling, DE", "intro": "Arrived"},
    ]
    new_items = old_items + [
        {"index": "trackitem_0_0", "sdate": "2025-08-18 10:19:00", "place": "Obertraubling, DE", "intro": "Out For Delivery Today"}
    ]
    old_summary = {"latest_time": "2025-08-16 06:00:00", "latest_place": "Obertraubling, DE", "latest_intro": "Processing", "status_code": "100"}
    new_summary = {"latest_time": "2025-08-18 10:19:00", "latest_place": "Obertraubling, DE", "latest_intro": "OFD", "status_code": "101"}

    d = compute_diff(old_summary, new_summary, old_items, new_items)
    print(pretty_print_diff(d))
