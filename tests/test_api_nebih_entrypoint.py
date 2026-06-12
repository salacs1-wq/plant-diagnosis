from fastapi.testclient import TestClient

from nebih_api import app


client = TestClient(app)


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


def test_pesticide_info_product_includes_related_data() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"product_name": "Racer", "limit": 20},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["active_substances"]
    assert payload["usages"]
    assert payload["documents"]
    assert {"Racer", "Racer 25 EC", "Racer 250 EC"} <= {
        item["product_name"] for item in payload["usages"]
    }


def test_pesticide_info_active_substance_returns_products_and_usages() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"active_substance": "azoxistrobin", "limit": 20},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["products"]
    assert payload["usages"]
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
    usage = payload["usages"][0]

    assert {
        "owner",
        "manufacturer",
        "representative",
        "expiry_date",
        "latest_document",
    } <= set(usage)
    assert "Bayer" in usage["manufacturer"]
    assert "Bayer Hung" in usage["representative"]


def test_pesticide_info_manufacturer_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"manufacturer": "Sharda Cropchem", "limit": 5},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all(
        "sharda cropchem" in item["manufacturer"].lower()
        for item in payload["usages"]
    )


def test_pesticide_info_representative_filter() -> None:
    response = client.get(
        "/action/pesticide-info",
        params={"representative": "Syngenta Kft", "limit": 5},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["usages"]
    assert all(
        "syngenta kft" in item["representative"].lower()
        for item in payload["usages"]
    )
