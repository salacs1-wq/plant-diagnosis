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
    }
    assert client.post("/diagnose", json={}).status_code == 404
    assert client.post("/diagnose-dp", json={}).status_code == 404


def test_nebih_entrypoint_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
