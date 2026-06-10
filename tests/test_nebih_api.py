import hashlib

from fastapi.testclient import TestClient

from main import app
from nebih_api import database_path


client = TestClient(app)


def file_digest() -> tuple[int, int, str]:
    path = database_path()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def test_products_search_is_accent_insensitive() -> None:
    response = client.get(
        "/products/search",
        params={"q": "Amistar", "permit_type": "parhuzamos"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] > 0
    assert any(item["product_name"] == "Amistar" for item in payload["items"])


def test_usage_search() -> None:
    response = client.get(
        "/usage/search",
        params={"product_name": "Kasumin 2 L", "crop": "vöröshagyma"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["min_interval_days"] == "7"


def test_product_by_permit_number_with_slash() -> None:
    response = client.get("/product/35042/2001")
    assert response.status_code == 200
    payload = response.json()
    assert any(item["product_name"] == "Amistar" for item in payload["products"])
    assert payload["usage_count"] > 0


def test_documents_by_permit_number() -> None:
    response = client.get("/documents/6300/2289-2/2022")
    assert response.status_code == 200
    assert response.json()["total"] > 0


def test_active_substance_search() -> None:
    response = client.get(
        "/active-substances/search",
        params={"q": "dikamba", "limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] > 0
    assert all(
        "dikamba" in item["active_substance_name"].casefold()
        for item in payload["items"]
    )


def test_nebih_database_stays_read_only() -> None:
    before = file_digest()
    for endpoint in (
        "/products/search?q=Amistar",
        "/usage/search?crop=kukorica&limit=2",
        "/product/35042/2001",
        "/documents/6300/2289-2/2022",
        "/active-substances/search?q=dikamba&limit=2",
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
    assert file_digest() == before
