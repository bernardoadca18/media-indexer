async def test_project_and_session_ingestion_preserve_raw(client) -> None:
    project = (await client.post("/projects", json={"name": "Witcher"})).json()
    raw = " primeira nota  \r\n\r\nsegunda\r\nlinha\r\n"
    response = await client.post(
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

    repeated = await client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "ignored"},
        files={
            "video": ("other.mp4", b"other", "video/mp4"),
            "notes": ("other.txt", b"other", "text/plain"),
        },
        headers={"Idempotency-Key": "session-001"},
    )
    assert repeated.json()["session"]["id"] == payload["session"]["id"]

    detail = await client.get(f"/sessions/{payload['session']['id']}")
    assert detail.status_code == 200
    assert detail.json()["raw_notes"] == raw
    assert (await client.get(f"/jobs/{payload['job']['id']}")).status_code == 200


async def test_missing_project_is_404(client) -> None:
    import uuid

    response = await client.get(f"/projects/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_import_notes_to_existing_session(client) -> None:
    """Test importing notes to a video-only session."""
    project = (await client.post("/projects", json={"name": "Notes Test"})).json()

    # Create session with video only (no notes)
    response = await client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "video-only"},
        files={
            "video": ("test.mp4", b"fake-video", "video/mp4"),
        },
        headers={"Idempotency-Key": "video-only-001"},
    )
    assert response.status_code == 202
    session = response.json()["session"]
    assert session["notes_filename"] is None

    # Import notes to the existing session
    notes_content = "First note\n\nSecond note\n"
    import_response = await client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/notes",
        files={"notes": ("notes.txt", notes_content.encode(), "text/plain")},
    )
    assert import_response.status_code == 200
    updated = import_response.json()
    assert updated["notes_filename"] == "notes.txt"
    assert [n["raw"] for n in updated["notes"]] == ["First note", "Second note\n"]


async def test_import_notes_rejects_empty_file(client) -> None:
    """Test that empty notes file is rejected."""
    project = (await client.post("/projects", json={"name": "Empty Notes Test"})).json()

    # Create session with video only
    response = await client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "video-only"},
        files={
            "video": ("test.mp4", b"fake-video", "video/mp4"),
        },
        headers={"Idempotency-Key": "empty-notes-001"},
    )
    session = response.json()["session"]

    # Try to import empty notes
    import_response = await client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/notes",
        files={"notes": ("empty.txt", b"", "text/plain")},
    )
    assert import_response.status_code == 400
