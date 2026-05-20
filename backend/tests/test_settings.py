from settings import get_cors_origins


def test_get_cors_origins_includes_local_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_adds_configured_origins(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://example.vercel.app, https://admin.example.com ",
    )

    assert get_cors_origins() == [
        "http://localhost:5173",
        "https://example.vercel.app",
        "https://admin.example.com",
    ]
