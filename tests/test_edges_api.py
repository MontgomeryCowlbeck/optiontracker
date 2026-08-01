async def test_create_and_get_edge(auth_client):
    resp = await auth_client.post("/api/journal/edges", json={
        "name": "Validated Long",
        "criteria": "Williams %R <= -80 + rVWAP lower band + ADX <= 25",
        "regime": "range",
    })
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "Validated Long"
    assert created["status"] == "developing"  # default

    got = await auth_client.get(f"/api/journal/edges/{created['id']}")
    assert got.status_code == 200
    assert got.json()["criteria"].startswith("Williams")


async def test_list_edges(auth_client):
    await auth_client.post("/api/journal/edges", json={"name": "A"})
    await auth_client.post("/api/journal/edges", json={"name": "B"})
    resp = await auth_client.get("/api/journal/edges")
    assert resp.status_code == 200
    names = {m["name"] for m in resp.json()["edges"]}
    assert {"A", "B"} <= names


async def test_duplicate_name_conflicts(auth_client):
    await auth_client.post("/api/journal/edges", json={"name": "Dup"})
    resp = await auth_client.post("/api/journal/edges", json={"name": "Dup"})
    assert resp.status_code == 409


async def test_update_edge(auth_client):
    created = (await auth_client.post("/api/journal/edges", json={"name": "M"})).json()
    resp = await auth_client.put(f"/api/journal/edges/{created['id']}", json={
        "status": "validated", "notes": "works live"})
    assert resp.status_code == 200
    got = (await auth_client.get(f"/api/journal/edges/{created['id']}")).json()
    assert got["status"] == "validated"
    assert got["notes"] == "works live"


async def test_delete_edge(auth_client):
    created = (await auth_client.post("/api/journal/edges", json={"name": "Gone"})).json()
    resp = await auth_client.delete(f"/api/journal/edges/{created['id']}")
    assert resp.status_code == 200
    assert (await auth_client.get(f"/api/journal/edges/{created['id']}")).status_code == 404


async def test_get_missing_404(auth_client):
    assert (await auth_client.get("/api/journal/edges/9999")).status_code == 404
