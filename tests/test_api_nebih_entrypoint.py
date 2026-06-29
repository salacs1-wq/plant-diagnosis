import sqlite3
import sys

from fastapi.testclient import TestClient

from nebih_api import app, fold_text


client = TestClient(app)
nebih_pesticide_info_api = sys.modules["nebih_pesticide_info_api"]


def test_nebih_entrypoint_exposes_only_expected_api_paths() -> None:
    api_paths = set(app.openapi()["paths"])
    assert api_paths == {
        "/health",
        "/products/search",
        "/usage/search",
        "/product/{permit_number}",
        "/documents/{permit_number}",
        "/active-substances/search",
        "/action/products",
        "/action/usage",
        "/action/dose",
        "/action/documents",
        "/action/pesticide-info",
    }
    assert client.post("/diagnose", json={}).status_code == 404
    assert client.post("/diagnose-dp", json={}).status_code == 404


def test_nebih_entrypoint_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_nebih_entrypoint_docs() -> None:
    response = client.get("/docs")
    assert response.status_code == 200


def test_action_products_racer() -> None:
    response = client.get(
        "/action/products",
        params={"q": "Racer", "limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 5
    assert len(payload["items"]) == 5
    assert payload["items"][0]["product_name"] == "Racer"
    assert set(payload["items"][0]) == {
        "product_name",
        "permit_number",
        "crop",
        "target",
        "dose",
        "dose_unit",
        "bbch",
        "phi",
        "max_treatments",
        "source_pdf",
    }


def test_action_usage_racer_sunflower() -> None:
    response = client.get(
        "/action/usage",
        params={
            "product_name": "Racer",
            "crop": "napraforgó",
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 5
    assert all(item["crop"] == "napraforgó" for item in payload["items"])
    assert all(item["dose"] == "2-3" for item in payload["items"])
    assert all(item["dose_unit"] == "l/ha" for item in payload["items"])
    assert all(item["source_pdf"] for item in payload["items"])


def test_action_documents() -> None:
    response = client.get(
        "/action/documents",
        params={"permit_number": "11831/2002", "limit": 2},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert all(item["source_pdf"] for item in payload["items"])


def test_action_dose_racer_without_crop_returns_distinct_crops() -> None:
    response = client.get(
        "/action/dose",
        params={"product_name": "Racer", "limit": 20},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 12
    assert {item["crop"] for item in payload["items"]} == {
        "burgonya",
        "kapor",
        "napraforgó",
        "sárgarépa, petrezselyem",
    }
    assert {item["product_name"] for item in payload["items"]} == {
        "Racer",
        "Racer 25 EC",
        "Racer 250 EC",
    }


def test_action_dose_racer_sunflower() -> None:
    response = client.get(
        "/action/dose",
        params={
            "product_name": "Racer",
            "crop": "napraforgó",
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 3
    assert {item["product_name"] for item in payload["items"]} == {
        "Racer",
        "Racer 25 EC",
        "Racer 250 EC",
    }
    for item in payload["items"]:
        assert item["crop"] == "napraforgó"
        assert item["dose"] == "2-3"
        assert item["dose_unit"] == "l/ha"
        assert item["bbch"] == "0-8"
        assert "max_treatments" in item
        assert item["source_pdf"]


def test_action_dose_product_fallback_is_accent_insensitive() -> None:
    response = client.get(
        "/action/dose",
        params={"product_name": "Rácér", "limit": 20},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 12
    assert {item["product_name"] for item in payload["items"]} == {
        "Racer",
        "Racer 25 EC",
        "Racer 250 EC",
    }


def test_action_dose_unknown_product_is_handled() -> None:
    response = client.get(
        "/action/dose",
        params={"product_name": "Biztosan nem létező készítmény", "limit": 20},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["items"] == []
    assert payload["error"]


def test_action_validation_errors_are_200() -> None:
    for endpoint in (
        "/action/products",
        "/action/products?q=Racer&limit=999",
        "/action/usage?limit=bad",
        "/action/dose?limit=bad",
        "/action/documents",
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is False
        assert payload["items"] == []
        assert payload["error"]


def test_pesticide_info_product_meta_excludes_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"product_name": "Racer", "limit": 20},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["active_substances"]
    assert payload["usages"] == []
    assert payload["documents"]
    assert payload["query"]["query_type"] == "META"
    assert {"Racer", "Racer 25 EC", "Racer 250 EC"} <= {
        item["product_name"] for item in payload["products"]
    }


def test_pesticide_info_active_substance_is_meta_search() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"active_substance": "azoxistrobin", "limit": 20},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["usages"] == []
    assert payload["query"]["query_type"] == "META"
    assert any(
        "azoxistrobin" in item["active_substance_name"].lower()
        for item in payload["active_substances"]
    )


def test_pesticide_info_crop_target_and_purpose() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "crop": "kukorica",
            "target": "magrol kelo egysziku gyomok",
            "purpose": "gyomirto",
            "limit": 20,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all("kukorica" in item["crop"].lower() for item in payload["usages"])
    assert all("gyom" in item["target"].lower() for item in payload["usages"])


def test_pesticide_info_bbch_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "crop": "kukorica",
            "bbch": 16,
            "purpose": "gyomirto",
            "limit": 20,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all(item["bbch_match"] == "match" for item in payload["usages"])


def test_pesticide_info_permission_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "crop": "kukorica",
            "purpose": "gyomirto",
            "akg_allowed": "true",
            "limit": 20,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert all(item["akg_allowed"] is True for item in payload["products"])


def test_pesticide_info_missing_search_terms_is_200() -> None:
    response = client.get("/action/pesticide-info")
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["products"] == []
    assert payload["active_substances"] == []
    assert payload["usages"] == []
    assert payload["documents"] == []
    assert payload["error"]


def test_pesticide_info_corteva_apple() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"company": "Corteva", "crop": "alma"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all("corteva" in item["owner"].lower() for item in payload["usages"])
    assert {item["product_name"] for item in payload["usages"]} == {
        "Fontelis 20 SC",
        "Laser",
        "Laser Duplo (er.név: Spin Tor)",
        "Nexsuba",
    }
    assert not any(
        item["product_name"] == "Closer 120 SC" for item in payload["usages"]
    )


def test_pesticide_info_corteva_pome_fruit_alias() -> None:
    for crop in ("almatermésűek", "almástermésűek"):
        response = client.get(
            "/action/pesticide-info",
            params={"company": "Corteva", "crop": crop},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["ok"] is True
        assert {item["product_name"] for item in payload["usages"]} == {
            "Fontelis 20 SC",
            "Laser",
            "Laser Duplo (er.név: Spin Tor)",
            "Nexsuba",
        }


def test_pesticide_info_laser_apple_usage_is_restored() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"product_name": "Laser", "crop": "alma"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    apple_usages = {
        item["product_name"]: item
        for item in payload["usages"]
        if crop_matches_for_test(item["crop"], "alma")
    }
    assert apple_usages["Laser"]["dose"] == "0.5"
    assert apple_usages["Laser Duplo (er.név: Spin Tor)"]["dose"] == "0.25"


def crop_matches_for_test(crop: str, search: str) -> bool:
    normalized = crop.casefold()
    return search in normalized or "almatermésű" in normalized


def test_pesticide_info_bayer_maize() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"company": "Bayer", "crop": "kukorica"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all(
        "bayer"
        in " ".join(
            (
                item["owner"],
                item["manufacturer"],
                item["representative"],
            )
        ).lower()
        for item in payload["usages"]
    )
    assert all("kukorica" in item["crop"].lower() for item in payload["usages"])


def test_pesticide_info_syngenta_rape_insecticide() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "company": "Syngenta",
            "crop": "repce",
            "purpose": "rovarolo",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all(
        "syngenta"
        in " ".join(
            (
                item["owner"],
                item["manufacturer"],
                item["representative"],
            )
        ).lower()
        for item in payload["usages"]
    )
    assert all("repce" in item["crop"].lower() for item in payload["usages"])
    assert all("rovar" in item["purpose"].lower() for item in payload["usages"])


def test_pesticide_info_deltamethrin() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"active_substance": "deltametrin"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["active_substances"]
    assert all(
        "deltametrin" in item["active_substance_name"].lower()
        for item in payload["active_substances"]
    )


def test_pesticide_info_sunflower_ragweed_category_fallback() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"crop": "napraforgo", "target": "parlagfu"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all("napraforg" in item["crop"].lower() for item in payload["usages"])
    assert "broader weed category" in payload["summary"]["note"]


def test_pesticide_info_company_fields_are_explicit() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"product_name": "Adengo", "limit": 1},
    )
    payload = response.json()
    product = payload["products"][0]

    assert {
        "owner",
        "manufacturer",
        "representative",
        "expiry_date",
        "latest_document",
    } <= set(product)
    assert payload["usages"] == []
    assert "Bayer" in product["manufacturer"]
    assert "Bayer Hung" in product["representative"]


def test_pesticide_info_manufacturer_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"manufacturer": "Sharda Cropchem", "limit": 5},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["usages"] == []
    assert all(
        "sharda cropchem" in item["manufacturer"].lower()
        for item in payload["products"]
    )


def test_pesticide_info_representative_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"representative": "Syngenta Kft", "limit": 5},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["usages"] == []
    assert all(
        "syngenta kft" in item["representative"].lower()
        for item in payload["products"]
    )


def test_pesticide_info_company_only_routes_to_meta() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"company": "Adama", "limit": 10},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["query"]["query_type"] == "META"
    assert payload["products"]
    assert payload["usages"] == []
    assert payload["summary"]["usage_count"] == 0


def test_pesticide_info_meta_does_not_read_usage_table(monkeypatch) -> None:
    real_connect = nebih_pesticide_info_api.connect

    def connect_without_usage_reads():
        connection = real_connect()

        def authorize(action, table, _column, _database, _trigger):
            if action == sqlite3.SQLITE_READ and table == "usage":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(
        nebih_pesticide_info_api,
        "connect",
        connect_without_usage_reads,
    )
    payload = client.get(
        "/action/pesticide-info",
        params={"company": "Adama", "limit": 5},
    ).json()

    assert payload["ok"] is True
    assert payload["query"]["query_type"] == "META"
    assert payload["usages"] == []


def test_pesticide_info_company_and_crop_routes_to_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"company": "Adama", "crop": "napraforgo", "limit": 10},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["query"]["query_type"] == "USAGE"
    assert payload["usages"]


def test_pesticide_info_question_type_controls_routing() -> None:
    meta = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Adengo",
            "question_type": "active_substance",
        },
    ).json()
    usage = client.get(
        "/action/pesticide-info",
        params={"product_name": "Adengo", "question_type": "dose"},
    ).json()

    assert meta["query"]["query_type"] == "META"
    assert meta["active_substances"]
    assert meta["usages"] == []
    assert usage["query"]["query_type"] == "USAGE"
    assert usage["usages"]


def test_pesticide_info_product_and_crop_routes_to_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Sumi Alfa 5 EC",
            "crop": "burgonya",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["query"]["query_type"] == "USAGE"
    assert payload["usages"]


def test_pesticide_info_permit_metadata_filters() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"permit_number": "02.5/568/2/2009", "limit": 5},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["query"]["query_type"] == "META"
    assert payload["products"]
    assert payload["usages"] == []


def test_pesticide_info_decis_forte_returns_full_verified_usage_scope() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Decis Forte",
            "question_type": "usage",
            "limit": 100,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 60
    assert {item["product_name"] for item in payload["usages"]} == {
        "Decis Forte",
        "Detector",
        "NUYARD",
    }
    assert all(
        item["verification_status"] == "VERIFIED_USAGE"
        for item in payload["usages"]
    )


def test_pesticide_info_adengo_dose_is_verified_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Adengo",
            "question_type": "dose",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["usages"]
    assert {
        (
            fold_text(item["crop"]),
            item["dose"],
            item["dose_unit"],
            item["verification_status"],
        )
        for item in payload["usages"]
    } == {
        ("kukorica (szemes, silo, vetomag)", "0.33-0.44", "l/ha", "VERIFIED_USAGE")
    }


def test_pesticide_info_sunflower_ragweed_uses_verified_broader_category() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "crop": "hagyomanyos napraforgo",
            "target": "parlagfu",
            "purpose": "gyomirto",
            "bbch": 14,
            "question_type": "recommendation",
            "limit": 100,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert {fold_text(item["product_name"]) for item in payload["usages"]} == {
        "fox (er.nev: modown 4 f)",
        "viballa",
    }
    assert all(item["bbch_match"] == "match" for item in payload["usages"])
    assert all(
        item["verification_status"] == "VERIFIED_USAGE"
        for item in payload["usages"]
    )


def test_pesticide_info_sumi_alfa_grape_leafhopper_small_use_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Sumi Alfa 5 EC",
            "crop": "szolo",
            "target": "amerikai szolokaboca",
            "question_type": "usage",
            "limit": 20,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 3
    assert {item["product_name"] for item in payload["usages"]} == {
        "Sumi Alfa 5 EC",
        "Sumi-Alpha 050 EC",
        "Wizard",
    }
    assert payload["usages"][0]["dose"] == "0.2-0.3"
    assert payload["usages"][0]["dose_unit"] == "l/ha"
    assert payload["usages"][0]["bbch"] == "55-79"
    assert payload["usages"][0]["phi"] == "7"
    assert payload["usages"][0]["max_treatments"] == "2"
    assert payload["usages"][0]["verification_status"] == "VERIFIED_USAGE"


def test_pesticide_info_karate_grape_leafhopper_includes_reference_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Karate Zeon 5 CS",
            "crop": "szolo",
            "target": "amerikai szolokaboca",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 12
    assert {item["product_name"] for item in payload["usages"]} == {
        "Full 5 CS",
        "Karate Zeon",
        "Karate Zeon 050 CS",
        "Karate Zeon 5 CS",
        "Kendo 5 CS",
        "Ninja Zeon 5 CS",
    }
    assert all(item["dose"] == "0.25" for item in payload["usages"])
    assert all(item["dose_unit"] == "l/ha" for item in payload["usages"])


def test_pesticide_info_mospilan_grape_leafhopper_includes_reference_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Mospilan 20 SG",
            "crop": "szolo",
            "target": "amerikai szolokaboca",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 6
    assert {item["product_name"] for item in payload["usages"]} == {
        "Gazelle 20 SG",
        "Mospilan 20 SG",
        "Mospilan 20 SG Original",
        "Mospilan SG",
        "Rafting",
        "Spilan 20 SG",
    }
    assert all(item["dose"] == "0.25-0.375" for item in payload["usages"])
    assert all(item["dose_unit"] == "kg/ha" for item in payload["usages"])


def test_pesticide_info_coragen_grape_moths_includes_reference_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Coragen 20 SC",
            "crop": "szolo",
            "target": "szolomolyok",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 9
    assert "Coragen 20 SC" in {item["product_name"] for item in payload["usages"]}
    assert "Voliam" in {item["product_name"] for item in payload["usages"]}
    assert all(item["dose"] == "150-175" for item in payload["usages"])
    assert all(item["dose_unit"] == "ml/ha" for item in payload["usages"])
    assert all(item["phi"] == "30" for item in payload["usages"])


def test_pesticide_info_benevia_greenhouse_pepper_small_use_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Benevia",
            "crop": "paprika",
            "target": "uveghazi molytetu",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["crop"] == "paprika (hajtatott)"
    assert usage["dose"] == "0.75"
    assert usage["dose_unit"] == "l/ha"
    assert usage["phi"] == "1"
    assert usage["bbch"] == "12-89"
    assert usage["max_treatments"] == "4"


def test_pesticide_info_benevia_greenhouse_cucumber_thrips_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Benevia",
            "crop": "uborka",
            "target": "dohanytripsz",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["crop"] == "uborka (hajtatott)"
    assert usage["dose"] == "75-100"
    assert usage["dose_unit"] == "ml/hl"
    assert usage["phi"] == "1"
    assert usage["bbch"] == "12-89"


def test_pesticide_info_benevia_strawberry_spotted_wing_drosophila_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Benevia",
            "crop": "szamoca",
            "target": "foltosszarnyu muslica",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["crop"] == "szamóca (hajtatott)"
    assert usage["dose"] == "75-100"
    assert usage["dose_unit"] == "ml/hl"
    assert usage["phi"] == "1"
    assert usage["bbch"] == "12-89"


def test_pesticide_info_deltam_onion_aphids_small_use_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Deltam",
            "crop": "voroshagyma",
            "target": "leveltetvek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["crop"] == "Vöröshagyma"
    assert usage["target"] == "levéltetvek"
    assert usage["dose"] == "8"
    assert usage["dose_unit"] == "ml/100m2"
    assert usage["bbch"] == "45"


def test_pesticide_info_deltam_cucumber_whiteflies_small_use_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Deltam",
            "crop": "uborka",
            "target": "liszteskek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert "uborka" in usage["crop"].casefold()
    assert "liszteskék" in usage["target"]
    assert usage["dose"] == "5"
    assert usage["dose_unit"] == "ml/100m2"
    assert usage["bbch"] == "70"


def test_pesticide_info_mospilan_sunflower_aphids_includes_reference_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Mospilan 20 SG",
            "crop": "napraforgo",
            "target": "leveltetvek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 6
    assert {item["product_name"] for item in payload["usages"]} == {
        "Gazelle 20 SG",
        "Mospilan 20 SG",
        "Mospilan 20 SG Original",
        "Mospilan SG",
        "Rafting",
        "Spilan 20 SG",
    }
    assert all(item["crop"] == "napraforgó" for item in payload["usages"])
    assert all(item["dose"] == "0.15-0.2" for item in payload["usages"])
    assert all(item["dose_unit"] == "kg/ha" for item in payload["usages"])
    assert all(item["bbch"] == "40-53" for item in payload["usages"])


def test_pesticide_info_gazelle_sunflower_aphids_stays_specific() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Gazelle 20 SG",
            "crop": "napraforgo",
            "target": "leveltetvek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Gazelle 20 SG"
    assert usage["crop"] == "napraforgó"
    assert usage["dose"] == "0.15-0.2"
    assert usage["dose_unit"] == "kg/ha"


def test_pesticide_info_deltaphar_leek_aphids_includes_related_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Deltaphar 25 EC",
            "crop": "porehagyma",
            "target": "leveltetu",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 3
    assert {item["product_name"] for item in payload["usages"]} == {
        "Deltaphar 25 EC",
        "Deltathrin",
        "Splendour",
    }
    assert all(item["crop"] == "Póréhagyma" for item in payload["usages"])
    assert all(item["dose"] == "0.5" for item in payload["usages"])
    assert all(item["dose_unit"] == "liter/ha" for item in payload["usages"])
    assert all(item["bbch"] == "12-45" for item in payload["usages"])


def test_pesticide_info_scatto_cucumber_whiteflies_includes_deltastar() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Scatto",
            "crop": "uborka",
            "target": "liszteskek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 4
    assert {item["product_name"] for item in payload["usages"]} == {
        "DeltaStar",
        "Scatto",
    }
    assert {item["dose"] for item in payload["usages"]} == {"0.1-0.18", "0.3-0.5"}
    assert all(item["dose_unit"] == "l/ha" for item in payload["usages"])


def test_pesticide_info_romble_onion_downy_mildew_includes_related_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Romble",
            "crop": "voroshagyma",
            "target": "peronoszpora",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 3
    assert {item["product_name"] for item in payload["usages"]} == {
        "Aster",
        "AzoMax CB 250 SC",
        "Romble",
    }
    assert all(
        item["crop"] == "vöröshagyma, fokhagyma, mogyoróhagyma szabadföldi"
        for item in payload["usages"]
    )
    assert all(item["dose"] == "0.75-1" for item in payload["usages"])
    assert all(item["dose_unit"] == "l/ha" for item in payload["usages"])
    assert all(item["bbch"] == "14" for item in payload["usages"])


def test_pesticide_info_trunfo_carrot_powdery_mildew_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Trunfo",
            "crop": "sargarepa",
            "target": "lisztharmat",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Trunfo"
    assert usage["crop"] == "sárgarépa szabadföldi"
    assert usage["dose"] == "1"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "16"


def test_pesticide_info_leptostar_rape_pollen_beetle_handles_compact_target() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Leptostar 200 SL",
            "crop": "kaposztarepce",
            "target": "repcefenybogar",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 2
    assert {item["product_name"] for item in payload["usages"]} == {
        "Aceptendo 200 SL",
        "Leptostar 200 SL",
    }
    assert all("repce-fénybogár" in item["target"] for item in payload["usages"])
    assert all(item["dose"] == "0.2-0.3" for item in payload["usages"])
    assert all(item["dose_unit"] == "l/ha" for item in payload["usages"])
    assert all(item["bbch"] == "20-59" for item in payload["usages"])


def test_pesticide_info_aceptendo_rape_pollen_beetle_stays_specific() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Aceptendo 200 SL",
            "crop": "kaposztarepce",
            "target": "repcefenybogar",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Aceptendo 200 SL"
    assert usage["dose"] == "0.2-0.3"
    assert usage["dose_unit"] == "l/ha"


def test_pesticide_info_roubaix_onion_downy_mildew_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Roubaix",
            "crop": "voroshagyma",
            "target": "peronoszpora",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Roubaix"
    assert "vöröshagyma" in usage["crop"].casefold()
    assert "peronoszpóra" in usage["target"]
    assert usage["dose"] == "0.75-1"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "14"


def test_pesticide_info_divam_leek_aphids_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Divam Pro",
            "crop": "porehagyma",
            "target": "leveltetu",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Divam Pro (er.név: Demetrina 25 EC)"
    assert usage["crop"] == "Póréhagyma"
    assert usage["dose"] == "0.5"
    assert usage["dose_unit"] == "liter/ha"
    assert usage["bbch"] == "12-45"


def test_pesticide_info_laser_duplo_apple_codling_moth_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Laser Duplo",
            "crop": "alma",
            "target": "almamoly",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Laser Duplo (er.név: Spin Tor)"
    assert usage["crop"] == "alma, körte, birs, naspolya"
    assert usage["dose"] == "0.25"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "15-80"


def test_pesticide_info_nexsuba_potato_beetle_inherits_laser_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Nexsuba",
            "crop": "burgonya",
            "target": "burgonyabogar",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Nexsuba"
    assert usage["crop"] == "burgonya"
    assert usage["dose"] == "0.15"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "48"


def test_pesticide_info_teppeki_coriander_handles_hyphenated_crop() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Teppeki",
            "crop": "koriander",
            "target": "leveltetu",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 4
    assert {item["product_name"] for item in payload["usages"]} == {
        "Afinto",
        "Hinode",
        "Teppeki",
        "Teppeki 50 WG",
    }
    assert all("korian- der" in item["crop"] for item in payload["usages"])
    assert all(item["dose"] == "0.16" for item in payload["usages"])
    assert all(item["dose_unit"] == "kg/ha" for item in payload["usages"])


def test_pesticide_info_coragen_carrot_fly_includes_related_products() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Coragen 20 SC",
            "crop": "sargarepa",
            "target": "sargarepalegy",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 9
    assert "CoragenPro" in {item["product_name"] for item in payload["usages"]}
    assert all(item["dose"] == "125-150" for item in payload["usages"])
    assert all(item["dose_unit"] == "ml/ha" for item in payload["usages"])
    assert all(item["bbch"] == "14-49" for item in payload["usages"])


def test_pesticide_info_turex_apple_winter_moth_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Turex WG",
            "crop": "alma",
            "target": "kis teliaraszolo",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Turex WG"
    assert usage["crop"] == "alma, körte"
    assert usage["dose"] == "1"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "11-87"


def test_pesticide_info_dagonis_potato_alternaria_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Dagonis",
            "crop": "burgonya",
            "target": "alternarias",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Dagonis"
    assert usage["crop"] == "burgonya (szabadföldi)"
    assert usage["dose"] == "0.6-0.75"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "14"


def test_pesticide_info_sivanto_parallel_carrot_aphids_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Sivanto Prime 200 SL",
            "crop": "sargarepa",
            "target": "leveltetvek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Sivanto Prime 200 SL"
    assert usage["crop"] == "sárgarépa"
    assert usage["dose"] == "0.625"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "14-45"


def test_pesticide_info_amistar_top_cabbage_has_recovered_bbch_row() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Amistar Top",
            "crop": "kaposztafelek",
            "target": "peronoszpora",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert any(
        item["product_name"] == "Amistar Top"
        and item["dose"] == "0.6-1"
        and item["dose_unit"] == "l/ha"
        and item["bbch"] == "41"
        for item in payload["usages"]
    )


def test_pesticide_info_usage_pagination_reports_next_offset() -> None:
    first = client.get(
        "/action/pesticide-info",
        params={
            "crop": "napraforgo",
            "target": "parlagfu",
            "question_type": "usage",
            "limit": 5,
        },
    ).json()
    second = client.get(
        "/action/pesticide-info",
        params={
            "crop": "napraforgo",
            "target": "parlagfu",
            "question_type": "usage",
            "limit": 5,
            "offset": 5,
        },
    ).json()

    assert first["ok"] is True
    assert first["summary"]["usage_count"] == 5
    assert first["summary"]["total_usage_count"] > 5
    assert first["summary"]["has_more"] is True
    assert first["summary"]["next_offset"] == 5
    assert first["summary"]["status"] == "AMBIGUOUS_LIMITED"
    assert second["ok"] is True
    assert second["summary"]["offset"] == 5
    assert second["summary"]["next_offset"] == 10
    assert [item["product_name"] for item in first["usages"]] != [
        item["product_name"] for item in second["usages"]
    ]


def test_pesticide_info_cythrin_max_carrot_aphids_inherits_cyperkill_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "CYTHRIN MAX",
            "crop": "sargarepa",
            "target": "leveltetvek",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "CYTHRIN MAX"
    assert usage["dose"] == "50"
    assert usage["dose_unit"] == "ml/ha"
    assert usage["bbch"] == "14-49"


def test_pesticide_info_pennthiol_carrot_powdery_mildew_inherits_microthiol_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Pennthiol",
            "crop": "sargarepa",
            "target": "lisztharmat",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Pennthiol"
    assert usage["dose"] == "5"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "14"


def test_pesticide_info_champion_potato_blight_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Champion WG",
            "crop": "burgonya",
            "target": "burgonyavesz",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Champion WG"
    assert usage["dose"] == "2"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "15"


def test_pesticide_info_azbany_carrot_inherits_tazer_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Azbany",
            "crop": "sargarepa",
            "target": "lisztharmat",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Azbany"
    assert usage["dose"] == "1"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "16"


def test_pesticide_info_green_doctor_strawberry_inherits_polyversum_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Green Doctor",
            "crop": "szamoca",
            "target": "botritisz",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Green Doctor"
    assert usage["dose"] == "0.1-0.2"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "41"


def test_pesticide_info_badge_sc_potato_blight_is_verified() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Badge SC",
            "crop": "burgonya",
            "target": "burgonyavesz",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Badge SC"
    assert usage["dose"] == "2.5-3"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "15"


def test_pesticide_info_bolid_cabbage_inherits_makler_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Bolid 250 SE",
            "crop": "fejes kaposzta",
            "target": "alternarias",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Bolid 250 SE"
    assert usage["dose"] == "0.8"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "41"


def test_pesticide_info_bactospeine_cherry_inherits_dipel_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Bactospeine WG",
            "crop": "cseresznye",
            "target": "lombrago",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Bactospeine WG"
    assert usage["dose"] == "1-1.5"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "11-87"


def test_pesticide_info_texio_cucumber_inherits_teldor_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Texio",
            "crop": "uborka",
            "target": "botritisz",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Texio"
    assert usage["dose"] == "1"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "12"


def test_pesticide_info_mavrik_grape_leafhopper_inherits_klartan_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Mavrik 24 EW",
            "crop": "szolo",
            "target": "amerikai szolokaboca",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Mavrik 24 EW"
    assert usage["dose"] == "0.2-0.3"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "56-80"


def test_pesticide_info_wizard_corn_borer_inherits_sumi_alfa_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Wizard",
            "crop": "kukorica",
            "target": "kukoricamoly",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Wizard"
    assert usage["dose"] == "0.3"
    assert usage["dose_unit"] == "l/ha"
    assert usage["bbch"] == "17-69"


def test_pesticide_info_flovine_potato_inherits_folpan_usage() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={
            "product_name": "Flovine 80 WDG",
            "crop": "burgonya",
            "target": "fitoftora",
            "question_type": "usage",
            "limit": 50,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["summary"]["status"] == "VERIFIED_USAGE"
    assert payload["summary"]["usage_count"] == 1
    usage = payload["usages"][0]
    assert usage["product_name"] == "Flovine 80 WDG"
    assert usage["dose"] == "1.25-2"
    assert usage["dose_unit"] == "kg/ha"
    assert usage["bbch"] == "31"
