"""
stac_api.py  –  STAC API Hilfsfunktionen für ch.swisstopo.spezialbefliegungen

Importiert von 0_GUI_stac_gdwh_delete_Data.py.
Direkt nutzbar: python stac_api.py  (gibt Kurzinfo aus)
"""

import re
import requests
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple

try:
    from gdwh_api import _AOI_CENTROIDS as _STAC_AOI_CENTROIDS
except ImportError:
    _STAC_AOI_CENTROIDS = {}

# Firmenproxy für externe Verbindungen (data.geo.admin.ch / sys-data.int.bgdi.ch)
_PROXY = {
    "http":  "http://proxy-bvcol.admin.ch:8080",
    "https": "http://proxy-bvcol.admin.ch:8080",
}

COLLECTION_ID = "ch.swisstopo.spezialbefliegungen"

ENVIRONMENTS = {
    "INT":  "https://sys-data.int.bgdi.ch/api/stac/v0.9/",
    "PROD": "https://data.geo.admin.ch/api/stac/v0.9/",
}

AUFTRAGSTYPEN: Dict[str, str] = {
    "KRY (Kryosphäre)":   "kry",
    "RAM (Rapidmapping)": "ram",
    "Alle":               "",
}

EXT_PRESETS: List[Tuple[str, List[str]]] = [
    ("tif / tiff",      [".tif", ".tiff"]),
    ("copc.laz / laz",  [".copc.laz", ".laz"]),
    ("jpg / jpeg",      [".jpg", ".jpeg"]),
    ("png",             [".png"]),
    ("json",            [".json"]),
]


# ─── Interne Session-Funktionen ───────────────────────────────────────────────

def _session_get(url: str, auth: Tuple, params: dict = None) -> requests.Response:
    return requests.get(url, auth=auth, params=params,
                        proxies=_PROXY, verify=False, timeout=(30, 60))


def _session_delete(url: str, auth: Tuple) -> requests.Response:
    return requests.delete(url, auth=auth,
                           proxies=_PROXY, verify=False, timeout=(30, 60))


# ─── Öffentliche API-Funktionen ───────────────────────────────────────────────

def get_item_direct(base_url: str, auth: Tuple, item_id: str) -> Optional[Dict]:
    """Holt ein einzelnes Item per exakter ID. Gibt None bei 404 zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id.strip()}")
    r = _session_get(url, auth)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_collection_items(base_url: str, auth: Tuple, log_fn=print) -> List[Dict]:
    """Holt alle Items der Collection mit Paginierung."""
    all_items = []
    url    = urljoin(base_url, f"collections/{COLLECTION_ID}/items")
    params = {"limit": 1000}
    while url:
        r = _session_get(url, auth, params)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("features", []))
        nxt = next((lk for lk in data.get("links", []) if lk.get("rel") == "next"), None)
        if nxt:
            url    = nxt["href"]
            params = None
            log_fn(f"  Paginierung … bisher {len(all_items)} Items geladen\n")
        else:
            url = None
    return all_items


def filter_items(items: List[Dict], search_term: str = "") -> List[Dict]:
    """Filtert Items nach Teilstring in der ID (case-insensitive)."""
    if not search_term:
        return items
    term = search_term.lower()
    return [item for item in items if term in item.get("id", "").lower()]


def delete_asset(base_url: str, auth: Tuple,
                 item_id: str, asset_key: str) -> Tuple[bool, int]:
    """Löscht einen einzelnen Asset. Gibt (Erfolg, HTTP-Statuscode) zurück."""
    url = urljoin(base_url,
                  f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}")
    r = _session_delete(url, auth)
    return r.status_code in (200, 204), r.status_code


def delete_item(base_url: str, auth: Tuple, item_id: str) -> Tuple[bool, int]:
    """Löscht ein Item vollständig (nur wenn leer). Gibt (Erfolg, HTTP-Statuscode) zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id}")
    r = _session_delete(url, auth)
    return r.status_code in (200, 204), r.status_code


def check_asset_status(href: str, auth: Tuple) -> int:
    """HEAD-Request auf Asset-URL. Gibt HTTP-Statuscode zurück, negativ bei Fehler."""
    if not href:
        return -1
    try:
        r = requests.head(href, proxies=_PROXY, verify=False,
                          timeout=(5, 15), allow_redirects=True)
        if r.status_code in (401, 403):
            r = requests.head(href, auth=auth, proxies=_PROXY, verify=False,
                              timeout=(5, 15), allow_redirects=True)
        return r.status_code
    except requests.exceptions.Timeout:
        return -2
    except Exception:
        return -3


def stac_item_year(item: Dict) -> str:
    """Extrahiert das Jahr aus properties.datetime oder der Item-ID."""
    src = item.get("properties", {}).get("datetime", "") or item.get("id", "")
    m = re.search(r"\b(20\d{2})\b", src)
    return m.group(1) if m else ""


def stac_item_area(item: Dict) -> str:
    """Schätzt den AOI-Namen aus Properties oder Geometrie-Schwerpunkt.
    Sucht zuerst in properties, dann via WGS84-Schwerpunkt → nächste LV95-AOI."""
    props = item.get("properties", {})
    for key in ("area", "aoi", "area_name", "region"):
        val = str(props.get(key, "")).strip()
        if val:
            return val.upper()
    bbox = item.get("bbox")
    if bbox and len(bbox) >= 4 and _STAC_AOI_CENTROIDS:
        lon_c = (bbox[0] + bbox[2]) / 2
        lat_c = (bbox[1] + bbox[3]) / 2
        # Näherung WGS84 → LV95 (ausreichend für AOI-Suche)
        e_lv95 = 2_600_000 + (lon_c - 7.44) * 74_000
        n_lv95 = 1_200_000 + (lat_c - 46.95) * 111_000
        best, best_d = "", float("inf")
        for name, (ax, ay) in _STAC_AOI_CENTROIDS.items():
            d = (e_lv95 - ax) ** 2 + (n_lv95 - ay) ** 2
            if d < best_d:
                best_d, best = d, name
        return best
    return ""


if __name__ == "__main__":
    print("stac_api.py – STAC API Modul")
    print(f"  Collection:  {COLLECTION_ID}")
    print(f"  Umgebungen:  {list(ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:   DELETE asset, DELETE item, GET items")
