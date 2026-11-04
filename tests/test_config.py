import pytest

from agentops.config import Settings


@pytest.mark.parametrize("value,expected", [
    (None, False),
    ("", False),
    ("   ", False),
    ("your-gemini-api-key-here", False),   # untouched .env.example placeholder
    ("AIzaSyExampleExampleExampleExample123", True),
])
def test_has_api_key(value, expected):
    assert Settings(GOOGLE_API_KEY=value).has_api_key is expected


def test_secret_never_appears_in_repr():
    s = Settings(GOOGLE_API_KEY="AIzaSyExampleExampleExampleExample123")
    assert "AIzaSy" not in repr(s) and "AIzaSy" not in str(s)


def test_vertex_ai_is_keyless(monkeypatch):
    """On Cloud Run / Agent Engine there is no API key: the service account is the credential."""
    from agentops.config import apply_model_env

    s = Settings(GOOGLE_API_KEY=None, GOOGLE_GENAI_USE_VERTEXAI="true", GOOGLE_CLOUD_PROJECT="demo-proj",
                 GOOGLE_CLOUD_LOCATION="asia-south1")
    assert s.has_api_key is False and s.has_model_credentials is True and s.gemini_backend == "vertex-ai"
    assert Settings(GOOGLE_API_KEY=None, GOOGLE_GENAI_USE_VERTEXAI="true").has_model_credentials is False  # no project

    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyStrayKeyThatMustNotLeakThrough00")
    apply_model_env(s)
    import os
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true" and os.environ["GOOGLE_CLOUD_PROJECT"] == "demo-proj"
    assert "GOOGLE_API_KEY" not in os.environ  # a stray key cannot silently reroute traffic off Vertex


def test_ai_studio_env(monkeypatch):
    from agentops.config import apply_model_env

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    apply_model_env(Settings(GOOGLE_API_KEY="AIzaSyExampleExampleExampleExample123"))
    import os
    assert os.environ["GOOGLE_API_KEY"].startswith("AIzaSy") and "GOOGLE_GENAI_USE_VERTEXAI" not in os.environ
