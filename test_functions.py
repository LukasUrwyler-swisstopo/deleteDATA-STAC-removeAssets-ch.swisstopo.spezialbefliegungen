"""
test_functions.py  –  pytest Tests für stac_api.py und gdwh_api.py

Ausführen:
    pytest test_functions.py -v
    pytest test_functions.py -v --tb=short   # kompakter Fehler-Output

Keine echten HTTP-Requests – alle Netzwerkaufrufe werden gemockt.
"""

import pytest
from unittest.mock import MagicMock, patch, call

import requests as req_module

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    filter_items,
    get_item_direct, get_collection_items,
    delete_asset, delete_item,
    check_asset_status,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS,
    gdwh_get_imports, gdwh_delete_import,
    gdwh_import_id, gdwh_import_name, gdwh_import_date, gdwh_import_status,
)

AUTH   = ("testuser", "testpass")
BASE   = "https://sys-data.int.bgdi.ch/api/stac/v0.9/"
GDWH_BASE = "https://ltgdwhi.adr.admin.ch/gdwh-api/v2/"


def _mock_response(status: int = 200, json_data=None, raise_on_status=False):
    """Hilfsfunktion: erstellt ein gefaktes requests.Response-Objekt."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    if raise_on_status:
        r.raise_for_status.side_effect = req_module.HTTPError(response=r)
    else:
        r.raise_for_status = MagicMock()
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ═══════════════════════════════════════════════════════════════════════════════

class TestKonstanten:

    def test_collection_id(self):
        assert COLLECTION_ID == "ch.swisstopo.spezialbefliegungen"

    def test_environments_schluessel(self):
        assert "INT"  in ENVIRONMENTS
        assert "PROD" in ENVIRONMENTS

    def test_gdwh_environments_schluessel(self):
        assert "INT"  in GDWH_ENVIRONMENTS
        assert "PROD" in GDWH_ENVIRONMENTS

    def test_auftragstypen_vorhanden(self):
        assert "KRY (Kryosphäre)"   in AUFTRAGSTYPEN
        assert "RAM (Rapidmapping)" in AUFTRAGSTYPEN
        assert "Alle"               in AUFTRAGSTYPEN

    def test_ext_presets_nicht_leer(self):
        assert len(EXT_PRESETS) > 0
        for label, exts in EXT_PRESETS:
            assert isinstance(label, str)
            assert all(e.startswith(".") for e in exts)


# ═══════════════════════════════════════════════════════════════════════════════
# filter_items
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterItems:

    ITEMS = [
        {"id": "ch.swisstopo.spezialbefliegungen_kry_2024-08-20"},
        {"id": "ch.swisstopo.spezialbefliegungen_kry_2024-09-15"},
        {"id": "ch.swisstopo.spezialbefliegungen_ram_2024-07-01"},
    ]

    def test_kein_suchbegriff_gibt_alle_zurueck(self):
        assert filter_items(self.ITEMS, "") == self.ITEMS

    def test_teilstring_trifft_mehrere(self):
        result = filter_items(self.ITEMS, "kry")
        assert len(result) == 2
        assert all("kry" in i["id"] for i in result)

    def test_teilstring_trifft_einen(self):
        result = filter_items(self.ITEMS, "ram")
        assert len(result) == 1
        assert result[0]["id"].endswith("ram_2024-07-01")

    def test_datum_als_filter(self):
        result = filter_items(self.ITEMS, "2024-08-20")
        assert len(result) == 1

    def test_case_insensitive(self):
        assert filter_items(self.ITEMS, "KRY") == filter_items(self.ITEMS, "kry")

    def test_kein_treffer(self):
        assert filter_items(self.ITEMS, "xyz_nicht_vorhanden") == []

    def test_leere_liste(self):
        assert filter_items([], "kry") == []

    def test_item_ohne_id_feld(self):
        items = [{"id": "kry-001"}, {"properties": {}}]
        result = filter_items(items, "kry")
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# check_asset_status
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckAssetStatus:

    def test_leere_url_gibt_minus_1(self):
        assert check_asset_status("", AUTH) == -1

    def test_200_ok(self):
        with patch("stac_api.requests.head", return_value=_mock_response(200)):
            assert check_asset_status("https://example.com/file.tif", AUTH) == 200

    def test_404_nicht_gefunden(self):
        with patch("stac_api.requests.head", return_value=_mock_response(404)):
            assert check_asset_status("https://example.com/file.tif", AUTH) == 404

    def test_403_wird_mit_auth_wiederholt(self):
        """Bei 403 soll ein zweiter HEAD-Request mit Auth gesendet werden."""
        first  = _mock_response(403)
        second = _mock_response(200)
        with patch("stac_api.requests.head", side_effect=[first, second]) as mock_head:
            result = check_asset_status("https://example.com/file.tif", AUTH)
        assert result == 200
        assert mock_head.call_count == 2
        # Zweiter Aufruf muss Auth enthalten
        _, kwargs = mock_head.call_args
        assert kwargs.get("auth") == AUTH

    def test_timeout_gibt_minus_2(self):
        with patch("stac_api.requests.head",
                   side_effect=req_module.exceptions.Timeout):
            assert check_asset_status("https://example.com/file.tif", AUTH) == -2

    def test_netzwerkfehler_gibt_minus_3(self):
        with patch("stac_api.requests.head",
                   side_effect=ConnectionError("no route")):
            assert check_asset_status("https://example.com/file.tif", AUTH) == -3


# ═══════════════════════════════════════════════════════════════════════════════
# get_item_direct
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetItemDirect:

    ITEM = {
        "id": "test-item-001",
        "assets": {"nrgb_cog": {"href": "https://example.com/file.tif"}},
    }

    def test_item_gefunden(self):
        with patch("stac_api._session_get", return_value=_mock_response(200, self.ITEM)):
            result = get_item_direct(BASE, AUTH, "test-item-001")
        assert result == self.ITEM

    def test_item_nicht_gefunden_404(self):
        with patch("stac_api._session_get", return_value=_mock_response(404)):
            result = get_item_direct(BASE, AUTH, "existiert-nicht")
        assert result is None

    def test_item_id_wird_getrimmt(self):
        """Leerzeichen um die Item-ID sollen entfernt werden."""
        with patch("stac_api._session_get", return_value=_mock_response(200, self.ITEM)) \
                as mock_get:
            get_item_direct(BASE, AUTH, "  test-item-001  ")
        url = mock_get.call_args[0][0]
        assert "test-item-001" in url
        assert "  " not in url

    def test_url_enthaelt_collection_und_item(self):
        with patch("stac_api._session_get", return_value=_mock_response(200, self.ITEM)) \
                as mock_get:
            get_item_direct(BASE, AUTH, "item-abc")
        url = mock_get.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc" in url


# ═══════════════════════════════════════════════════════════════════════════════
# get_collection_items
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCollectionItems:

    def test_einzelne_seite(self):
        data = {"features": [{"id": "item-1"}, {"id": "item-2"}], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert len(result) == 2

    def test_paginierung_zwei_seiten(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}, {"id": "item-3"}], "links": []}

        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            result = get_collection_items(BASE, AUTH)

        assert len(result) == 3
        assert result[0]["id"] == "item-1"
        assert result[2]["id"] == "item-3"

    def test_leere_collection(self):
        data = {"features": [], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert result == []

    def test_log_fn_wird_bei_paginierung_aufgerufen(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}], "links": []}

        log_calls = []
        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            get_collection_items(BASE, AUTH, log_fn=lambda msg: log_calls.append(msg))

        assert len(log_calls) == 1
        assert "Paginierung" in log_calls[0]


# ═══════════════════════════════════════════════════════════════════════════════
# delete_asset
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteAsset:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 200

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 204

    def test_fail_403(self):
        with patch("stac_api._session_delete", return_value=_mock_response(403)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False
        assert code == 403

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)) \
                as mock_del:
            delete_asset(BASE, AUTH, "item-abc", "my_asset_key")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc/assets/my_asset_key" in url


# ═══════════════════════════════════════════════════════════════════════════════
# delete_item
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteItem:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code = delete_item(BASE, AUTH, "item-001")
        assert ok is True
        assert code == 200

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, _ = delete_item(BASE, AUTH, "item-001")
        assert ok is True

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code = delete_item(BASE, AUTH, "item-999")
        assert ok is False
        assert code == 404

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)) \
                as mock_del:
            delete_item(BASE, AUTH, "item-xyz")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-xyz" in url
        assert "/assets/" not in url


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen (pure, kein Mock nötig)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportId:

    def test_feld_id(self):
        assert gdwh_import_id({"id": "pkg-001"}) == "pkg-001"

    def test_fallback_datapackageId(self):
        assert gdwh_import_id({"datapackageId": "pkg-002"}) == "pkg-002"

    def test_fallback_package_id(self):
        assert gdwh_import_id({"package_id": "pkg-003"}) == "pkg-003"

    def test_prioritaet_id_vor_datapackageId(self):
        assert gdwh_import_id({"id": "A", "datapackageId": "B"}) == "A"

    def test_kein_feld_gibt_fragezeichen(self):
        assert gdwh_import_id({}) == "?"


class TestGdwhImportName:

    def test_feld_name(self):
        assert gdwh_import_name({"name": "mein_paket"}) == "mein_paket"

    def test_fallback_datapackageName(self):
        assert gdwh_import_name({"datapackageName": "paket_xyz"}) == "paket_xyz"

    def test_fallback_description(self):
        assert gdwh_import_name({"description": "Beschreibung"}) == "Beschreibung"

    def test_fallback_auf_id(self):
        assert gdwh_import_name({"id": "fallback-id"}) == "fallback-id"

    def test_kein_feld_gibt_fragezeichen(self):
        assert gdwh_import_name({}) == "?"

    def test_prioritaet_name_vor_description(self):
        assert gdwh_import_name({"name": "A", "description": "B"}) == "A"


class TestGdwhImportDate:

    def test_iso_datum_mit_t(self):
        assert gdwh_import_date({"date": "2024-08-20T10:30:00Z"}) == "2024-08-20 10:30"

    def test_datum_wird_auf_16_zeichen_gekuerzt(self):
        result = gdwh_import_date({"date": "2024-08-20T10:30:45.123Z"})
        assert result == "2024-08-20 10:30"

    def test_fallback_importDate(self):
        assert gdwh_import_date({"importDate": "2024-09-01T08:00:00"}) == "2024-09-01 08:00"

    def test_fallback_created_at(self):
        assert gdwh_import_date({"created_at": "2024-01-15T12:00:00"}) == "2024-01-15 12:00"

    def test_kein_feld_gibt_strich(self):
        assert gdwh_import_date({}) == "–"

    def test_prioritaet_date_vor_created_at(self):
        result = gdwh_import_date({"date": "2024-08-20T10:00:00", "created_at": "2023-01-01"})
        assert result.startswith("2024-08-20")


class TestGdwhImportStatus:

    def test_feld_status(self):
        assert gdwh_import_status({"status": "completed"}) == "completed"

    def test_fallback_state(self):
        assert gdwh_import_status({"state": "running"}) == "running"

    def test_fallback_importStatus(self):
        assert gdwh_import_status({"importStatus": "failed"}) == "failed"

    def test_kein_feld_gibt_leerstring(self):
        assert gdwh_import_status({}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_get_imports (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhGetImports:

    GDS_KEY = "ch.swisstopo.spezialbefliegungen-kry"

    def test_direkte_liste_als_antwort(self):
        data = [{"id": "pkg-1"}, {"id": "pkg-2"}]
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == data

    def test_wrapper_objekt_items(self):
        data = {"items": [{"id": "pkg-1"}], "total": 1}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"id": "pkg-1"}]

    def test_wrapper_objekt_imports(self):
        data = {"imports": [{"id": "pkg-1"}, {"id": "pkg-2"}]}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert len(result) == 2

    def test_wrapper_objekt_data(self):
        data = {"data": [{"id": "pkg-1"}]}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"id": "pkg-1"}]

    def test_leere_liste(self):
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, [])):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == []

    def test_url_korrekt_aufgebaut(self):
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, [])) \
                as mock_get:
            gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        url = mock_get.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports" in url

    def test_kein_auth_parameter(self):
        """GET soll ohne Auth-Parameter aufgerufen werden."""
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, [])) \
                as mock_get:
            gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        _, kwargs = mock_get.call_args
        assert "auth" not in kwargs

    def test_http_fehler_wird_weitergegeben(self):
        with patch("gdwh_api.requests.get",
                   return_value=_mock_response(500, raise_on_status=True)):
            with pytest.raises(req_module.HTTPError):
                gdwh_get_imports(GDWH_BASE, self.GDS_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_delete_import (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhDeleteImport:

    GDS_KEY = "ch.swisstopo.spezialbefliegungen-kry"
    PKG_ID  = "datapackage-abc-123"

    def test_job_objekt_wird_zurueckgegeben(self):
        job = {"id": "job-001", "status": "running", "progress": 0}
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, job)):
            result = gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
        assert result == job

    def test_mit_email_parameter(self):
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, {})) \
                as mock_del:
            gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID,
                               email="lukas@example.com")
        _, kwargs = mock_del.call_args
        assert kwargs["params"] == {"email": "lukas@example.com"}

    def test_ohne_email_kein_params(self):
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, {})) \
                as mock_del:
            gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
        _, kwargs = mock_del.call_args
        assert kwargs["params"] is None

    def test_auth_wird_uebergeben(self):
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, {})) \
                as mock_del:
            gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
        _, kwargs = mock_del.call_args
        assert kwargs["auth"] == AUTH

    def test_url_korrekt_aufgebaut(self):
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, {})) \
                as mock_del:
            gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
        url = mock_del.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports/{self.PKG_ID}" in url

    def test_nicht_json_antwort_gibt_status_dict(self):
        r = _mock_response(200)
        r.status_code = 200
        r.json.side_effect = ValueError("no json")
        with patch("gdwh_api.requests.delete", return_value=r):
            result = gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
        assert result == {"status": "200"}

    def test_http_fehler_401_wird_weitergegeben(self):
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(401, raise_on_status=True)):
            with pytest.raises(req_module.HTTPError):
                gdwh_delete_import(GDWH_BASE, AUTH, self.GDS_KEY, self.PKG_ID)
