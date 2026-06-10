from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_health_contract_is_unchanged() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_contract_is_unchanged() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "plantnet-only-v4-safe",
        "endpoints": ["/diagnose", "/diagnose-dp", "/debug-env", "/health"],
    }


def test_debug_env_without_key(monkeypatch) -> None:
    monkeypatch.delenv("PLANTNET_API_KEY", raising=False)
    response = client.get("/debug-env")
    assert response.status_code == 200
    assert response.json() == {
        "plantnet_key_present": False,
        "plantnet_key_length": 0,
    }


def test_diagnose_existing_success_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "download_image", lambda _: b"image")
    monkeypatch.setattr(
        main,
        "call_plantnet_identify",
        lambda *_: {
            "results": [
                {
                    "score": 0.95,
                    "species": {
                        "scientificNameWithoutAuthor": "Ambrosia artemisiifolia",
                        "commonNames": ["parlagfű"],
                    },
                }
            ]
        },
    )
    response = client.post(
        "/diagnose",
        json={
            "openaiFileIdRefs": [
                {
                    "id": "file-1",
                    "download_link": "https://example.test/image.jpg",
                }
            ],
            "project": "k-middle-europe",
            "mode": "expert",
            "caseType": "weed",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["mode"] == "weed"
    assert payload["top_match"]["latin_name"] == "Ambrosia artemisiifolia"


def test_diagnose_dp_existing_success_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "download_image", lambda _: b"image")
    monkeypatch.setattr(
        main,
        "call_plantnet_diseases",
        lambda *_: {
            "results": [
                {
                    "score": 0.8,
                    "scientificName": "Puccinia triticina",
                    "commonName": "levélrozsda",
                }
            ]
        },
    )
    response = client.post(
        "/diagnose-dp",
        json={
            "openaiFileIdRefs": [
                {
                    "id": "file-1",
                    "download_link": "https://example.test/image.jpg",
                }
            ],
            "project": "k-middle-europe",
            "mode": "expert",
            "caseType": "disease",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["mode"] == "disease"
    assert payload["plantnet_top5"][0]["latin_name"] == "Puccinia triticina"
