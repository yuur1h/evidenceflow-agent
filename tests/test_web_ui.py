from fastapi.testclient import TestClient

from agent import app

client = TestClient(app, raise_server_exceptions=False)


def test_root_serves_guided_ui_and_post_root_still_is_the_api():
    page = client.get("/")
    assert page.status_code == 200
    assert "EvidenceFlow 研究助手" in page.text
    assert client.get("/app").text == page.text
    # The JSON API on POST / must survive sharing the path with the UI.
    assert client.post("/", json={}).status_code == 422


def test_ui_talks_to_the_endpoints_it_depends_on():
    page = client.get("/").text
    for path in ("/stream", "/env-status", "/visualize"):
        assert path in page
    assert client.get("/env-status").status_code == 200
    assert client.get("/visualize").status_code == 200
