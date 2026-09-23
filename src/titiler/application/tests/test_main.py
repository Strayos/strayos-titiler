"""Test titiler.application.main.app."""

from rio_tiler import __version__ as rio_tiler_version
import pytest

from titiler.application.settings import ApiSettings


def test_health(app):
    """Test /healthz endpoint."""
    response = app.get("/healthz")
    assert response.status_code == 200
    resp = response.json()
    assert set(resp["versions"].keys()) == {
        "titiler",
        "rio-tiler",
        "gdal",
        "geos",
        "proj",
        "rasterio",
    }
    assert resp["versions"]["rio-tiler"] == rio_tiler_version

    response = app.get("/api")
    assert response.status_code == 200

    response = app.get("/api.html")
    assert response.status_code == 200


def test_local_minio_mode_requires_endpoint(monkeypatch):
    """Hanka mode fails closed without an explicit storage endpoint."""
    monkeypatch.setenv("TITILER_API_LOCAL_MINIO_ONLY", "true")
    monkeypatch.delenv("TITILER_API_MINIO_ENDPOINT", raising=False)

    with pytest.raises(ValueError, match="TITILER_API_MINIO_ENDPOINT"):
        ApiSettings()
