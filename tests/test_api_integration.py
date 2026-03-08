"""
API integration tests against the live HeartClaws server.
Requires the server to be running on localhost:5020.
"""

import httpx
import pytest

BASE = "http://localhost:5020"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        yield c


# --------------- World endpoints ---------------


class TestWorldState:
    def test_full_world_state(self, client):
        r = client.get("/world/state")
        assert r.status_code == 200
        data = r.json()
        assert "world" in data
        assert "players" in data
        assert "structures" in data
        assert data["open_world"] is True

    def test_world_has_64_sectors(self, client):
        r = client.get("/world/state")
        sectors = r.json()["world"]["sectors"]
        assert len(sectors) == 64

    def test_sectors_have_biomes(self, client):
        r = client.get("/world/state")
        sectors = r.json()["world"]["sectors"]
        for sid, sec in sectors.items():
            assert sec["biome"] is not None, f"Sector {sid} has no biome"

    def test_sectors_have_types(self, client):
        r = client.get("/world/state")
        sectors = r.json()["world"]["sectors"]
        types = {sec["sector_type"] for sec in sectors.values()}
        assert "HAVEN" in types
        assert "SETTLED" in types or "FRONTIER" in types


class TestJoinAndPlay:
    """Full integration: join, get state, build, heartbeat, leaderboard."""

    def test_join_world(self, client):
        r = client.post("/world/join", json={"name": "TestBot"})
        assert r.status_code == 200
        data = r.json()
        assert "player_id" in data
        assert "sector_id" in data
        self.__class__.player_id = data["player_id"]
        self.__class__.sector_id = data["sector_id"]

    def test_player_state(self, client):
        pid = getattr(self.__class__, "player_id", None)
        if not pid:
            pytest.skip("join failed")
        r = client.get(f"/world/state/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["player"]["player_id"] == pid
        assert data["player"]["alive"] is True
        assert data["player"]["metal"] >= 0

    def test_build_tower(self, client):
        pid = getattr(self.__class__, "player_id", None)
        sector = getattr(self.__class__, "sector_id", None)
        if not pid:
            pytest.skip("join failed")
        r = client.post(
            "/world/action",
            json={
                "player_id": pid,
                "action_type": "BUILD_STRUCTURE",
                "payload": {"sector_id": sector, "structure_type": "TOWER"},
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["accepted"] is True

    def test_manual_heartbeat(self, client):
        r = client.post("/world/heartbeat")
        assert r.status_code == 200
        data = r.json()
        assert "heartbeat" in data
        assert isinstance(data["heartbeat"], int)

    def test_leaderboard(self, client):
        r = client.get("/world/leaderboard")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # At least our test player should be on leaderboard
        pid = getattr(self.__class__, "player_id", None)
        if pid:
            ids = [entry["player_id"] for entry in data]
            assert pid in ids

    def test_season_info(self, client):
        r = client.get("/world/season")
        assert r.status_code == 200
        data = r.json()
        assert "season" in data
        assert "remaining" in data


class TestDiplomacy:
    def test_send_message(self, client):
        # Get existing players
        r = client.get("/world/state")
        players = list(r.json()["players"].keys())
        if len(players) < 2:
            pytest.skip("need 2 players")
        r = client.post(
            "/world/message",
            json={
                "from_player_id": players[0],
                "to_player_id": players[1],
                "message": "Hello from integration test!",
            },
        )
        assert r.status_code == 200

    def test_read_messages(self, client):
        r = client.get("/world/state")
        players = list(r.json()["players"].keys())
        if len(players) < 2:
            pytest.skip("need 2 players")
        r = client.get(f"/world/messages/{players[1]}")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_set_policy(self, client):
        r = client.get("/world/state")
        players = list(r.json()["players"].keys())
        if len(players) < 2:
            pytest.skip("need 2 players")
        r = client.post(
            "/world/action",
            json={
                "player_id": players[0],
                "action_type": "SET_POLICY",
                "payload": {"target_player_id": players[1], "stance": "ALLY"},
            },
        )
        assert r.status_code == 200


class TestScanAndHistory:
    def test_scan_sector(self, client):
        r = client.get("/world/state")
        players = list(r.json()["players"].keys())
        if not players:
            pytest.skip("no players")
        r = client.post(
            "/world/action",
            json={
                "player_id": players[0],
                "action_type": "SCAN_SECTOR",
                "payload": {"sector_id": "H_4_4"},
            },
        )
        assert r.status_code == 200

    def test_history(self, client):
        r = client.get("/world/history?limit=10&offset=0")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert isinstance(data["events"], list)


class TestWorldStats:
    def test_stats_endpoint(self, client):
        r = client.get("/world/stats")
        assert r.status_code == 200
        data = r.json()
        assert "heartbeat" in data
        assert "total_players_alltime" in data
        assert "alive_players" in data
        assert "active_players" in data
        assert "inactive_players" in data
        assert "total_structures" in data
        assert "structures_by_type" in data
        assert "total_sectors" in data
        assert data["total_sectors"] == 64
        assert "controlled_sectors" in data
        assert "unclaimed_sectors" in data
        assert "total_actions" in data
        assert "total_messages" in data
        assert "season" in data
        assert "players" in data
        assert isinstance(data["players"], list)


class TestErrorHandling:
    def test_invalid_player_state(self, client):
        r = client.get("/world/state/nonexistent_player")
        assert r.status_code in (404, 400)

    def test_invalid_action(self, client):
        r = client.post(
            "/world/action",
            json={
                "player_id": "nonexistent",
                "action_type": "BUILD_STRUCTURE",
                "payload": {"sector_id": "H_0_0", "structure_type": "TOWER"},
            },
        )
        # Should either 400/422 or return accepted=False
        if r.status_code == 200:
            assert r.json()["accepted"] is False
        else:
            assert r.status_code in (400, 422, 500)

    def test_duplicate_join_name(self, client):
        # Join twice with same name - should still work (different player_id)
        r1 = client.post("/world/join", json={"name": "DupeBot"})
        r2 = client.post("/world/join", json={"name": "DupeBot"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["player_id"] != r2.json()["player_id"]


class TestFrontend:
    def test_index_html_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "HeartClaws" in r.text

    def test_index_has_openworld_mode(self, client):
        r = client.get("/")
        assert "openworld" in r.text
        assert "Open World" in r.text

    def test_index_has_hex_grid(self, client):
        r = client.get("/")
        assert "hex" in r.text.lower() or "grid" in r.text.lower()
