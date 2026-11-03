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
