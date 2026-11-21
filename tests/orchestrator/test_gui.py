from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.app import mount_gui


def test_gui_is_served_under_ui_with_root_redirect(tmp_path):
    (tmp_path / "index.html").write_text("<title>Fabric</title>")
    app = FastAPI()

    assert mount_gui(app, tmp_path)
    client = TestClient(app)

    assert "Fabric" in client.get("/ui/").text
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/ui/"


def test_no_gui_without_a_build(tmp_path):
    app = FastAPI()

    assert not mount_gui(app, tmp_path)
    assert TestClient(app).get("/ui/").status_code == 404
