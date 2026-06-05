from src.security.url_safety import sanitize_external_url


def test_sanitize_external_url_allows_http_and_https():
    assert sanitize_external_url("https://example.com/path") == "https://example.com/path"
    assert sanitize_external_url("http://example.com") == "http://example.com"


def test_sanitize_external_url_rejects_unsafe_schemes():
    assert sanitize_external_url("javascript:alert(1)") is None
    assert sanitize_external_url("data:text/html;base64,SGVsbG8=") is None
    assert sanitize_external_url("file:///etc/passwd") is None


def test_sanitize_external_url_rejects_missing_host_or_blank():
    assert sanitize_external_url("https:///missing-host") is None
    assert sanitize_external_url("   ") is None
    assert sanitize_external_url(None) is None
