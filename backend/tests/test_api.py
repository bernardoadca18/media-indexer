def test_project_and_session_ingestion_preserve_raw(client) -> None:
    project = client.post("/projects", json={"name": "Witcher"}).json()
    raw = " primeira nota  \r\n\r\nsegunda\r\nlinha\r\n"
    response = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "001"},
        files={
            "video": ("001.mp4", b"fake-video", "video/mp4"),
            "notes": ("001.txt", raw.encode(), "text/plain"),
        },
        headers={"Idempotency-Key": "session-001"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["session"]["raw_notes"] == raw
    assert [n["raw"] for n in payload["session"]["notes"]] == [
        " primeira nota  ",
        "segunda\r\nlinha\r\n",
    ]
    assert payload["session"]["media"]["storage_uri"].startswith("local://")
    assert payload["job"]["status"] == "pending"

    repeated = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "ignored"},
        files={
            "video": ("other.mp4", b"other", "video/mp4"),
            "notes": ("other.txt", b"other", "text/plain"),
        },
        headers={"Idempotency-Key": "session-001"},
    )
    assert repeated.json()["session"]["id"] == payload["session"]["id"]

    detail = client.get(f"/sessions/{payload['session']['id']}")
    assert detail.status_code == 200
    assert detail.json()["raw_notes"] == raw
    assert client.get(f"/jobs/{payload['job']['id']}").status_code == 200


def test_missing_project_is_404(client) -> None:
    import uuid

    response = client.get(f"/projects/{uuid.uuid4()}")
    assert response.status_code == 404
