"""One-shot generator for typed scan + filter catalogs.

Reads three sources at repo root:
  - scanner_reference.json   (IBKR's categorized scanner reference)
  - scan-parameters.xml      (gateway's authoritative scan parameters)
  - scanner_params.json      (flat dump of all scan codes + filters)

Emits:
  - src/connector/data/scan_catalog.json
  - src/connector/data/filter_catalog.json

Re-runnable: regenerate when IBKR adds new scans (replace source files first).
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
SRC_REF = REPO / "scanner_reference.json"
SRC_XML = REPO / "scan-parameters.xml"
SRC_PARAMS = REPO / "scanner_params.json"
OUT_SCANS = REPO / "src" / "connector" / "data" / "scan_catalog.json"
OUT_FILTERS = REPO / "src" / "connector" / "data" / "filter_catalog.json"

# Strategy tagging rules. Applied to the lowercased
# (code + name + description + category) text per scan. Multiple tags per
# scan are allowed. Tags are *intent* lookups for the LLM; categories are
# IBKR's taxonomy. Related but not identical.
STRATEGY_RULES: dict[str, list[str]] = {
    # Polarized intents — only tag the side the trader actually wants.
    "value": [
        "low p/e",
        "low price/book",
        "low price/tang",
        "low price/sales",
        "low price/cash",
        "low ev",
        "low enterprise",
    ],
    "overvalued": [
        "high p/e",
        "high price/book",
        "high price/tang",
        "high price/sales",
        "high price/cash",
    ],
    "growth": [
        "growth rate",
        "high growth",
        "earnings growth",
        "revenue growth",
        "high eps",
        "eps chg",
        "eps change",
        "rev change",
        "revenue change",
        "percentage growth",
        "per share growth",
        "sps growth",
        "sales growth",
    ],
    "quality": [
        "return on equity",
        "return on assets",
        "high quick ratio",
        "high current ratio",
        "high roe",
        "high roa",
        "high operating margin",
        "high net profit margin",
        "high gross margin",
        "average quality",
    ],
    "income": ["dividend", "yield", "payout ratio"],
    "momentum_up": [
        "% gainer",
        "gainers since open",
        "close-to-open % gainer",
        "after-hours gainer",
        "top % up",
    ],
    "momentum_down": [
        "% loser",
        "losers since open",
        "close-to-open % loser",
        "after-hours loser",
        "top % down",
    ],
    "activity": [
        "most active",
        "hot by volume",
        "highest volume",
        "trade count",
        "unusual volume",
    ],
    "options_flow": [
        "option volume",
        "opt volume",
        "implied vol",
        "put/call",
        "open interest",
        "imp vol",
        "imp volat",
    ],
    "volatility": [
        "implied vol",
        "imp volat",
        "iv percentile",
        "hv percentile",
        "historical volat",
        "historical vol",
    ],
    "events": [
        "upcoming",
        "recent meeting",
        "recent earnings",
        "recent event",
        "ex-date",
        "dividend ex",
        "analyst meeting",
        "wsh ",  # Wall Street Horizon prefix
        "next earnings",
        "previous earnings",
        "previous event",
        "next event",
        "next major event",
    ],
    "analyst": [
        "analyst rating",
        "analyst price target",
        "ratings",
        "price target",
        "target/price",
    ],
    "technical": [
        "ema",
        "macd",
        "rsi",
        "bollinger",
        "moving average",
        "vs 13w",
        "vs 26w",
        "vs 52w",
        "above ema",
        "below ema",
        "ppo",
        "bullish",
        "bearish",
        "histogram",
        "stochastic",
    ],
    "gap": ["gap", "open gap"],
    "high_low": [
        "13w high",
        "13w low",
        "26w high",
        "26w low",
        "52w high",
        "52w low",
        "near high",
        "near low",
        "week high",
        "week low",
        "weeks high",
        "weeks low",
    ],
    "esg": ["esg", "carbon", "sustainability"],
    "short_interest": [
        "short interest",
        "short ratio",
        "days to cover",
        "% of float short",
    ],
    "insider": ["insider", "shares held by insider"],
    "institutional": [
        "institutional",
        "institution",
        "institutional ownership",
    ],
    "imbalance": ["imbalance", "buy imbalance", "sell imbalance"],
    "bonds": [
        "bond",
        "coupon",
        "maturity",
        "yield to",
        "spread",
        "moody",
        "s&p rating",
    ],
    "etf": [
        "etf ",
        "expense ratio",
        "tracking error",
        "altar score",
        "assets under management",
    ],
    "risk_adjusted": [
        "sharpe",
        "sortino",
        "treynor",
        "information ratio",
        "return risk",
        "risk ratio",
        "max gain",
        "max loss",
        "max drawdown",
    ],
    "correlation": ["correlation", "covariance"],
    "leverage": [
        "debt / shareholders",
        "debt / equity",
        "debt/equity",
        "total assets / total equity",
        "leverage ratio",
        "lt debt",
        "long term debt",
        "fund leverage",
    ],
    "efficiency": [
        "return on investment",
        "return on assets",
        "asset turnover",
        "cash per share",
        "price to cash",
    ],
    "shortable": [
        "shortable",
        "fee rate",
        "utilization",
        "borrow",
        "stock loan",
    ],
    "score": [
        "z-score",
        "z score",
        "total return score",
        "altar score",
        "event score",
    ],
    "fundamentals": [
        # Catch-all tag — anything that touches a fundamental metric gets
        # this regardless of high/low polarity, so the LLM can ask "what
        # fundamental scans exist?" and get the full menu.
        "p/e",
        "price/book",
        "price/tang",
        "price/sales",
        "price/cash",
        "price to cash",
        "roe",
        "roa",
        "margin",
        "eps",
        "rev change",
        "revenue",
        "dividend",
        "payout",
        "quick ratio",
        "current ratio",
        "growth rate",
        "ev/ebitda",
        "enterprise value",
        "book value",
        "debt",
        "equity",
        "sales per share",
        "total return",
        "expense ratio",
    ],
}


def _detect_inverse(code: str, all_codes: set[str]) -> str | None:
    """Return the inverse scan code if one exists in the catalog, else None.

    Patterns:
      HIGH_X        <-> LOW_X
      X_ASC         <-> X_DESC
      *PERC_GAIN*   <-> *PERC_LOSE*
    """
    if code.startswith("HIGH_"):
        candidate = "LOW_" + code[5:]
        if candidate in all_codes:
            return candidate
    elif code.startswith("LOW_"):
        candidate = "HIGH_" + code[4:]
        if candidate in all_codes:
            return candidate
    if code.endswith("_ASC"):
        candidate = code[:-4] + "_DESC"
        if candidate in all_codes:
            return candidate
    if code.endswith("_DESC"):
        candidate = code[:-5] + "_ASC"
        if candidate in all_codes:
            return candidate
    if "PERC_GAIN" in code:
        candidate = code.replace("PERC_GAIN", "PERC_LOSE")
        if candidate in all_codes:
            return candidate
    if "PERC_LOSE" in code:
        candidate = code.replace("PERC_LOSE", "PERC_GAIN")
        if candidate in all_codes:
            return candidate
    return None


def _tag_strategies(text: str) -> list[str]:
    haystack = text.lower()
    return [
        tag for tag, keywords in STRATEGY_RULES.items()
        if any(k in haystack for k in keywords)
    ]


def _xml_vendor_map(xml_path: Path) -> dict[str, str]:
    """scanCode -> vendor (ALV, WSH, Refinitiv, etc.) from scan-parameters.xml."""
    out: dict[str, str] = {}
    if not xml_path.exists():
        return out
    tree = ET.parse(xml_path)
    for st in tree.getroot().iter("ScanType"):
        code = (st.findtext("scanCode") or "").strip()
        vendor = (st.findtext("vendor") or "").strip()
        if code:
            out[code] = vendor
    return out


def _params_instrument_map(params_path: Path) -> dict[str, list[str]]:
    """scanCode -> machine instrument list (STK, ETF.EQ.US, ...) from scanner_params.json."""
    if not params_path.exists():
        return {}
    data = json.loads(params_path.read_text())
    return {s["code"]: list(s.get("instruments", [])) for s in data.get("scan_codes", [])}


def build_scan_catalog() -> dict:
    ref = json.loads(SRC_REF.read_text())
    vendors = _xml_vendor_map(SRC_XML)
    instrument_codes = _params_instrument_map(SRC_PARAMS)

    flat: list[dict] = []
    seen: set[str] = set()
    for category, scans in ref.get("scanners_by_category", {}).items():
        for scan in scans:
            code = scan["code"]
            if code in seen:
                continue  # guard against any cross-category collisions
            seen.add(code)
            flat.append(
                {
                    "code": code,
                    "name": scan.get("name", ""),
                    "description": scan.get("description", ""),
                    "category": category,
                    "vendor": vendors.get(code, ""),
                    "instruments_human": list(scan.get("instruments", [])),
                    "instruments": instrument_codes.get(code, []),
                }
            )

    all_codes = {s["code"] for s in flat}
    for s in flat:
        s["inverse_of"] = _detect_inverse(s["code"], all_codes)
        s["strategy_tags"] = _tag_strategies(
            f"{s['code']} {s['name']} {s['description']} {s['category']}"
        )

    return {
        "_info": {
            "description": "Typed IBKR scan code catalog generated from scanner_reference.json + scan-parameters.xml + scanner_params.json",
            "source_files": [SRC_REF.name, SRC_XML.name, SRC_PARAMS.name],
            "scan_count": len(flat),
            "categories": sorted({s["category"] for s in flat}),
        },
        "scans": flat,
    }


def build_filter_catalog() -> dict:
    ref = json.loads(SRC_REF.read_text())
    return {
        "_info": {
            "description": "Filter parameter catalog: categories, per-instrument applicability, and reference.",
            "source_files": [SRC_REF.name],
        },
        "categories": ref.get("filter_categories", {}),
        "filters_by_instrument": ref.get("filters_by_instrument", {}),
        "filters_reference": ref.get("filters_reference", {}),
    }


def main() -> None:
    scans = build_scan_catalog()
    filters = build_filter_catalog()
    OUT_SCANS.parent.mkdir(parents=True, exist_ok=True)
    OUT_SCANS.write_text(json.dumps(scans, indent=2))
    OUT_FILTERS.write_text(json.dumps(filters, indent=2))
    print(
        f"Wrote {OUT_SCANS.relative_to(REPO)} "
        f"with {scans['_info']['scan_count']} scans across "
        f"{len(scans['_info']['categories'])} categories."
    )
    print(f"Wrote {OUT_FILTERS.relative_to(REPO)}.")


if __name__ == "__main__":
    main()
