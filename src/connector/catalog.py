"""Loaders for the pre-generated scan + filter catalogs.

The JSON files in this directory are produced by ``scripts/build_catalog.py``
from the three source files at the repo root (scanner_reference.json,
scan-parameters.xml, scanner_params.json). We load each once per process via
``@lru_cache`` and serve fast in-memory filtered lookups to the MCP tools.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
SCAN_CATALOG_PATH = DATA_DIR / "scan_catalog.json"
FILTER_CATALOG_PATH = DATA_DIR / "filter_catalog.json"


@lru_cache(maxsize=1)
def load_scan_catalog() -> dict:
    return json.loads(SCAN_CATALOG_PATH.read_text())


@lru_cache(maxsize=1)
def load_filter_catalog() -> dict:
    return json.loads(FILTER_CATALOG_PATH.read_text())


def search_scans(
    category: str | None = None,
    strategy: str | None = None,
    instrument: str | None = None,
    query: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Filter the typed scan catalog.

    All filters are AND-combined. ``category``/``strategy`` are exact
    case-insensitive matches against the catalog's category and strategy_tags
    fields. ``instrument`` is a substring match against the machine
    instruments list (e.g. "STK" matches "STK.US.MAJOR"). ``query`` is a
    case-insensitive substring across code, name, and description.
    """
    cat = (category or "").strip().lower()
    strat = (strategy or "").strip().lower()
    inst = (instrument or "").strip().upper()
    q = (query or "").strip().lower()
    out: list[dict] = []
    for s in load_scan_catalog().get("scans", []):
        if cat and s.get("category", "").lower() != cat:
            continue
        if strat and strat not in {t.lower() for t in s.get("strategy_tags", [])}:
            continue
        if inst and not any(inst in i.upper() for i in s.get("instruments", [])):
            continue
        if q:
            haystack = " ".join((s.get("code", ""), s.get("name", ""), s.get("description", ""))).lower()
            if q not in haystack:
                continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def list_categories() -> list[str]:
    return load_scan_catalog().get("_info", {}).get("categories", [])


def list_strategies() -> list[str]:
    tags: set[str] = set()
    for s in load_scan_catalog().get("scans", []):
        tags.update(s.get("strategy_tags", []))
    return sorted(tags)


def search_filters(
    category: str | None = None,
    instrument: str | None = None,
) -> dict:
    """Look up filter parameter codes by category, with a per-instrument note.

    The source's ``filters_by_instrument`` field is descriptive prose
    ("US Stocks - all filters available"), not a machine-readable
    allowlist — so this returns the full category list plus the
    instrument note for the LLM to read.
    """
    cat = load_filter_catalog()
    categories: dict[str, list[str]] = cat.get("categories", {})
    by_inst: dict[str, str] = cat.get("filters_by_instrument", {})
    ref: dict[str, dict] = cat.get("filters_reference", {})

    chosen: dict[str, list[str]] = (
        {category: categories.get(category, [])} if category else categories
    )

    result: dict = {
        "categories": chosen,
        "reference": {
            code: ref.get(code, {})
            for codes in chosen.values()
            for code in codes
        },
    }
    if instrument:
        result["instrument_note"] = by_inst.get(
            instrument,
            by_inst.get(instrument.upper(), "unknown instrument"),
        )
    return result
