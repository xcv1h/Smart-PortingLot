"""
API tests. They run against fakeredis, an in-memory Redis stand-in, so no
real Redis server is needed. fakeredis ships a Lua interpreter, so the EVAL
scripts in main.py run for real and the atomic park/release logic is what is
being tested, not a mock of it.
"""

from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import main

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    # Every test gets an empty "Redis", a frozen clock and the default rate.
    fake = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(main, "redis_client", fake)
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    monkeypatch.setattr(main, "RATE_PER_HOUR", 12.0)
    return TestClient(main.app)


def park(client, plate):
    return client.post("/cars", json={"plate": plate})


def test_park_car(client):
    r = park(client, "12345678")
    assert r.status_code == 201
    assert r.json() == {"port": "Gi0/1", "plate": "12345678", "entered_at": "2026-11-10T12:00:00Z"}


def test_dashes_are_removed(client):
    r = park(client, "123-45-678")
    assert r.status_code == 201
    assert r.json()["plate"] == "12345678"


def test_list_ports(client):
    park(client, "1111111")
    park(client, "2222222")
    r = client.get("/ports")
    assert r.status_code == 200
    body = r.json()
    assert body["capacity"] == main.CAPACITY
    assert [p["port"] for p in body["ports"]] == main.PORTS
    assert body["ports"][0] == {"port": "Gi0/1", "plate": "1111111", "entered_at": "2026-11-10T12:00:00Z"}
    assert body["ports"][1]["plate"] == "2222222"
    assert body["ports"][2] == {"port": "Gi0/3", "plate": None, "entered_at": None}


def test_release_car_and_fee(client, monkeypatch):
    park(client, "12345678")
    monkeypatch.setattr(main, "utc_now", lambda: NOW + timedelta(seconds=5400))  # 1.5 h later
    r = client.delete("/cars/12345678")
    assert r.status_code == 200
    assert r.json() == {"port": "Gi0/1", "plate": "12345678", "duration_seconds": 5400, "fee": 18.0}
    assert client.get("/ports").json()["ports"][0]["plate"] is None  # port is down again


def test_lowest_free_port_is_reused(client):
    for plate in ("1111111", "2222222", "3333333"):
        park(client, plate)
    client.delete("/cars/2222222")
    assert park(client, "4444444").json()["port"] == "Gi0/2"


def test_duplicate_plate(client):
    park(client, "12345678")
    r = park(client, "1234-5678")  # same plate, written differently
    assert r.status_code == 409
    assert r.json() == {"detail": "Plate 12345678 is already parked."}
    assert client.get("/ports").json()["ports"][1]["plate"] is None  # nothing was parked twice


@pytest.mark.parametrize("plate", ["123456", "123456789", "ABCD1234", "1234567a", "12 345 678", ""])
def test_invalid_plate(client, plate):
    r = park(client, plate)
    assert r.status_code == 422
    assert r.json() == {"detail": "Plate must be 7 or 8 digits, dashes are allowed."}


def test_malformed_body(client):
    r = client.post("/cars", json={"number": "12345678"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)  # one sentence, not FastAPI's error list


def test_full_lot(client):
    for n in range(main.CAPACITY):
        assert park(client, f"1000000{n}").status_code == 201
    r = park(client, "99999999")
    assert r.status_code == 507
    assert r.json() == {"detail": f"The lot is full, all {main.CAPACITY} ports are up."}


def test_release_unknown_plate(client):
    r = client.delete("/cars/12345678")
    assert r.status_code == 404
    assert r.json() == {"detail": "Plate 12345678 is not parked."}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_when_redis_is_down(client, monkeypatch):
    down = fakeredis.FakeServer()
    down.connected = False  # every command now raises redis.ConnectionError
    monkeypatch.setattr(main, "redis_client", fakeredis.FakeRedis(server=down))
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"detail": "Redis is not reachable."}
