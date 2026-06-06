"""Screener helpers: search scan-parameters.xml and run a scan via ib_async.

``scan-parameters.xml`` is bundled into the image at /app/scan-parameters.xml.
It is the same XML IBKR's TWS API returns from ``reqScannerParameters()`` and
lists every scanCode, instrument set, location, and column definition the
gateway recognises. We parse it lazily once per process and serve fast
text-search lookups so the LLM can discover valid scanCodes without us
hardcoding a list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_XML_PATH = Path(
    os.environ.get("SCAN_PARAMETERS_PATH", "/app/scan-parameters.xml")
)


@dataclass(frozen=True)
class ScanCode:
    scan_code: str
    display_name: str
    instruments: str
    vendor: str

    def as_dict(self) -> dict:
        return {
            "scanCode": self.scan_code,
            "displayName": self.display_name,
            "instruments": self.instruments,
            "vendor": self.vendor,
        }


@lru_cache(maxsize=1)
def _load_scan_codes() -> tuple[ScanCode, ...]:
    if not DEFAULT_XML_PATH.exists():
        raise FileNotFoundError(
            f"scan-parameters.xml not found at {DEFAULT_XML_PATH}. "
            "Mount or bake it into the image."
        )
    tree = ET.parse(DEFAULT_XML_PATH)
    root = tree.getroot()
    out: list[ScanCode] = []
    # ScanType elements live under .//ScanType — there are nested grouping
    # nodes (ScanTypeList, ScanCategory, etc.) so we use a recursive XPath.
    for st in root.iter("ScanType"):
        scan_code = (st.findtext("scanCode") or "").strip()
        if not scan_code:
            continue
        out.append(
            ScanCode(
                scan_code=scan_code,
                display_name=(st.findtext("displayName") or "").strip(),
                instruments=(st.findtext("instruments") or "").strip(),
                vendor=(st.findtext("vendor") or "").strip(),
            )
        )
    return tuple(out)


def search_scan_codes(
    query: str | None = None,
    instrument: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Substring search over loaded ScanTypes (case-insensitive).

    Matches against ``scanCode`` and ``displayName``. ``instrument`` filters
    rows whose instruments token list contains the supplied value as a
    substring (e.g. ``"STK"`` matches ``"STK.US.MAJOR"``).
    """
    q = (query or "").strip().lower()
    inst = (instrument or "").strip().upper()
    matches: list[dict] = []
    for sc in _load_scan_codes():
        if inst and inst not in sc.instruments.upper():
            continue
        if q and q not in sc.scan_code.lower() and q not in sc.display_name.lower():
            continue
        matches.append(sc.as_dict())
        if len(matches) >= limit:
            break
    return matches
