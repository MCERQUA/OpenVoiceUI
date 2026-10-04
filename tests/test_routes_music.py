"""
Tests for routes/music.py — Music Blueprint (P7-T1, ADR-010)
"""

import json
import pytest
from flask import Flask


@pytest.fixture(scope="module")
def music_client():
    """Minimal Flask app with music blueprint registered."""
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.music import music_bp
    app.register_blueprint(music_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestMusicHelpers:
    def test_reserve_track(self):
        from routes.music import reserve_track, get_reserved_track, clear_reservation
        track = {"name": "track_a.mp3", "path": "music/track_a.mp3"}
        reservation_id = reserve_track(track)
        assert reservation_id is not None
        reserved = get_reserved_track()
        assert reserved == track
        clear_reservation()

    def test_clear_reservation(self):
        from routes.music import reserve_track, get_reserved_track, clear_reservation
        track = {"name": "some_track.mp3", "path": "music/some_track.mp3"}
        reserve_track(track)
        clear_reservation()
        assert get_reserved_track() is None

    def test_get_reserved_track_none_initially(self):
        from routes.music import clear_reservation, get_reserved_track
        clear_reservation()
        assert get_reserved_track() is None

    def test_load_music_metadata_returns_dict(self):
        from routes.music import load_music_metadata
        meta = load_music_metadata()
        assert isinstance(meta, dict)

    def test_load_generated_music_metadata_returns_dict(self):
        from routes.music import load_generated_music_metadata
        meta = load_generated_music_metadata()
        assert isinstance(meta, dict)

    def test_get_music_files_returns_list(self):
        from routes.music import get_music_files
        files = get_music_files()
        assert isinstance(files, list)

    def test_get_music_files_generated_playlist(self):
        from routes.music import get_music_files
        files = get_music_files(playlist="generated")
        assert isinstance(files, list)

    def test_load_playlist_order_returns_list(self):
        from routes.music import load_playlist_order
        order = load_playlist_order("library")
        assert isinstance(order, list)


# ---------------------------------------------------------------------------
# API: /api/music?action=status
# ---------------------------------------------------------------------------

class TestMusicStatusEndpoint:
    def test_status_returns_200(self, music_client):
        resp = music_client.get("/api/music?action=status")
        assert resp.status_code == 200

    def test_status_returns_json(self, music_client):
        resp = music_client.get("/api/music?action=status")
        data = resp.get_json()
        assert data is not None

    def test_status_has_playing_key(self, music_client):
        resp = music_client.get("/api/music?action=status")
        data = resp.get_json()
        assert "playing" in data

    def test_status_has_volume_key(self, music_client):
        resp = music_client.get("/api/music?action=status")
        data = resp.get_json()
        assert "volume" in data


# ---------------------------------------------------------------------------
# API: /api/music?action=list
# ---------------------------------------------------------------------------

class TestMusicListEndpoint:
    def test_list_returns_200(self, music_client):
        resp = music_client.get("/api/music?action=list")
        assert resp.status_code == 200

    def test_list_returns_json(self, music_client):
        resp = music_client.get("/api/music?action=list")
        data = resp.get_json()
        assert data is not None


# ---------------------------------------------------------------------------
# API: /api/music?action=volume
# ---------------------------------------------------------------------------

class TestMusicVolumeEndpoint:
    def test_volume_set(self, music_client):
        resp = music_client.get("/api/music?action=volume&volume=0.5")
        assert resp.status_code == 200

    def test_volume_set_response_has_volume(self, music_client):
        resp = music_client.get("/api/music?action=volume&volume=0.3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_volume_clamped_min(self, music_client):
        resp = music_client.get("/api/music?action=volume&volume=-5")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API: /api/music?action=shuffle
# ---------------------------------------------------------------------------

class TestMusicShuffleEndpoint:
    def test_shuffle_toggle(self, music_client):
        resp = music_client.get("/api/music?action=shuffle")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API: /api/music/transition GET
# ---------------------------------------------------------------------------

class TestMusicTransitionEndpoint:
    def test_get_transition_returns_200(self, music_client):
        resp = music_client.get("/api/music/transition")
        assert resp.status_code == 200

    def test_get_transition_returns_json(self, music_client):
        resp = music_client.get("/api/music/transition")
        data = resp.get_json()
        assert data is not None


# ---------------------------------------------------------------------------
# API: /api/music/playlists
# ---------------------------------------------------------------------------

class TestMusicPlaylistsEndpoint:
    def test_list_playlists_returns_200(self, music_client):
        resp = music_client.get("/api/music/playlists")
        assert resp.status_code == 200

    def test_list_playlists_returns_json(self, music_client):
        resp = music_client.get("/api/music/playlists")
        data = resp.get_json()
        assert data is not None

    def test_list_playlists_has_playlists_key(self, music_client):
        resp = music_client.get("/api/music/playlists")
        data = resp.get_json()
        # API returns {"playlists": [...]}
        assert "playlists" in data

    def test_list_playlists_playlists_is_list(self, music_client):
        resp = music_client.get("/api/music/playlists")
        data = resp.get_json()
        assert isinstance(data.get("playlists"), list)


# ---------------------------------------------------------------------------
# Shuffle bag — every track plays once before any repeats
# ---------------------------------------------------------------------------

class TestShuffleBag:
    @pytest.fixture(autouse=True)
    def _bag_path(self, tmp_path, monkeypatch):
        import routes.music as m
        monkeypatch.setattr(m, "SHUFFLE_BAG_PATH", tmp_path / "music-shuffle-bags.json")
        self.m = m

    @staticmethod
    def _tracks(n):
        return [{"name": f"song-{i}", "filename": f"song-{i}.mp3"} for i in range(n)]

    def test_full_cycle_plays_every_track_once(self):
        tracks = self._tracks(12)
        picked, current = [], None
        for _ in range(12):
            t = self.m.shuffle_pick("library", tracks, exclude_name=current)
            picked.append(t["name"])
            current = t["name"]
        assert sorted(picked) == sorted(t["name"] for t in tracks)

    def test_control_plain_random_choice_repeats(self):
        # Negative control: the old picker (random.choice excluding only the
        # current track) does NOT cover a 12-track library in 12 picks.
        import random
        rng = random.Random(4)
        tracks = self._tracks(12)
        current, picked = None, []
        for _ in range(12):
            avail = [t for t in tracks if t["name"] != current]
            current = rng.choice(avail)["name"]
            picked.append(current)
        assert len(set(picked)) < 12

    def test_cycle_survives_reload_from_disk(self):
        tracks = self._tracks(6)
        first = {self.m.shuffle_pick("library", tracks)["name"] for _ in range(3)}
        assert self.m.SHUFFLE_BAG_PATH.exists()
        rest = {self.m.shuffle_pick("library", tracks)["name"] for _ in range(3)}
        assert first.isdisjoint(rest)
        assert first | rest == {t["name"] for t in tracks}

    def test_new_cycle_never_repeats_current_track(self):
        tracks = self._tracks(3)
        current = None
        for _ in range(30):
            t = self.m.shuffle_pick("library", tracks, exclude_name=current)
            assert t["name"] != current
            current = t["name"]

    def test_deleted_track_drops_out_and_new_track_joins(self):
        tracks = self._tracks(4)
        self.m.shuffle_pick("library", tracks)
        self.m.shuffle_pick("library", tracks)
        survivors = tracks[1:] + [{"name": "song-new", "filename": "song-new.mp3"}]
        seen = {self.m.shuffle_pick("library", survivors)["name"] for _ in range(4)}
        assert "song-0" not in seen
        assert "song-new" in seen

    def test_peek_does_not_consume(self):
        tracks = self._tracks(2)
        self.m.shuffle_pick("library", tracks, consume=False)
        self.m.shuffle_pick("library", tracks, consume=False)
        assert not self.m.SHUFFLE_BAG_PATH.exists()

    def test_explicit_play_counts_as_played(self):
        tracks = self._tracks(3)
        self.m.shuffle_mark_played("library", tracks[0])
        seen = {self.m.shuffle_pick("library", tracks)["name"] for _ in range(2)}
        assert seen == {"song-1", "song-2"}

    def test_playlists_have_separate_bags(self):
        tracks = self._tracks(2)
        self.m.shuffle_pick("library", tracks)
        self.m.shuffle_pick("library", tracks)
        # library cycle is complete; generated has its own untouched cycle
        g = {self.m.shuffle_pick("generated", tracks)["name"] for _ in range(2)}
        assert g == {"song-0", "song-1"}

    def test_empty_playlist_returns_none(self):
        assert self.m.shuffle_pick("library", []) is None
