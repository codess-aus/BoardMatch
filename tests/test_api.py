from fastapi.testclient import TestClient

from boardmatch.api import app

client = TestClient(app)


def test_index_serves_ui():
    response = client.get("/")
    assert response.status_code == 200
    assert "BoardMatch" in response.text


def test_candidate_endpoint():
    body = client.get("/api/candidate").json()
    assert body["name"] == "Priya Raman"
    assert body["skills"]


def test_opportunities_endpoint_paid_only():
    body = client.get("/api/opportunities", params={"paid_only": True}).json()
    assert body["count"] == body["paid_count"]
    first = body["results"][0]
    assert first["score"] >= body["results"][-1]["score"]
    assert "gap_actions" in first


def test_opportunity_detail_and_404():
    assert client.get("/api/opportunities/gov-001").json()["id"] == "gov-001"
    assert client.get("/api/opportunities/missing").status_code == 404


def test_intro_paths_endpoint():
    body = client.get("/api/opportunities/asx-101/intro-paths").json()
    assert body["paths"][0]["connection"] == "Alex Chen"


def test_coach_endpoints():
    cv = client.post("/api/coach/board-cv", params={"opportunity_id": "gov-002"}).json()
    assert "Priya Raman" in cv["content"]
    bio = client.post("/api/coach/bio").json()
    assert bio["kind"] == "director_bio"
    outreach = client.post(
        "/api/coach/outreach", params={"opportunity_id": "gov-002"}
    ).json()
    assert "Regional Water Corporation" in outreach["content"]


def test_tracker_and_readiness():
    response = client.post(
        "/api/tracker", json={"opportunity_id": "gov-003", "stage": "interviewing"}
    )
    assert response.status_code == 200
    assert response.json()["readiness_score"] > 0
    snapshot = client.get("/api/readiness").json()
    assert snapshot["stage_counts"]["interviewing"] >= 1


def test_tracker_rejects_unknown_opportunity():
    response = client.post(
        "/api/tracker", json={"opportunity_id": "nope", "stage": "applied"}
    )
    assert response.status_code == 404
