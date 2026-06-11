from fastapi.testclient import TestClient

from api_nebih import app


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
    }
    assert client.post("/diagnose", json={}).status_code == 404
    assert client.post("/diagnose-dp", json={}).status_code == 404


def test_nebih_entrypoint_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
