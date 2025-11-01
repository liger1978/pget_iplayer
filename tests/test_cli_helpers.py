import json
import os
import sys
import time
from pathlib import Path

import pytest
import types

from pget_iplayer import cli


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "bbc"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if 400 <= self.status_code:
            raise cli.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def install_fake_requests(monkeypatch, responses):
    def _fake_get(url, timeout):
        assert url in responses, f"Unexpected URL {url}"
        payload = responses[url]
        status_code = 200
        if isinstance(payload, tuple):
            payload, status_code = payload
        return FakeResponse(payload, status_code)

    monkeypatch.setattr(cli.requests, "get", _fake_get)


@pytest.fixture(autouse=True)
def stub_tqdm(monkeypatch):
    module = types.ModuleType("tqdm")

    class _DummyTqdm:
        def __init__(self, *args, **kwargs):
            self.n = 0.0
            self.total = kwargs.get("total", 0.0)
            self.pos = kwargs.get("position", 0)
            self.leave = kwargs.get("leave", True)
            self.bar_format = kwargs.get("bar_format", "")

        def set_description_str(self, *args, **kwargs):
            pass

        def update(self, amount):
            self.n += amount

        def refresh(self, *args, **kwargs):
            pass

        def close(self):
            pass

    def _dummy_write(*args, **kwargs):
        pass

    module.tqdm = _DummyTqdm
    module.write = _dummy_write
    original = sys.modules.get("tqdm")
    monkeypatch.setitem(sys.modules, "tqdm", module)
    yield
    if original is None:
        sys.modules.pop("tqdm", None)


@pytest.fixture
def temp_cwd(tmp_path):
    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(original)


def test_normalise_pid_prefers_last_match_with_digits():
    value = "See https://bbc.example/P0ABC123 and also Q0ZXY999"
    assert cli._normalise_pid(value) == "q0zxy999"


def test_normalise_pid_fallback_when_no_match():
    assert cli._normalise_pid(" MixedCase ") == "mixedcase"


def test_normalise_pid_from_iplayer_episode_url():
    url = (
        "https://www.bbc.co.uk/iplayer/episode/B03TZLWV/"
        "shaun-the-sheep-series-4-2-caught-short-alien?seriesId=b006z39g"
    )
    assert cli._normalise_pid(url) == "b03tzlwv"


def test_normalise_pid_from_iplayer_series_url_returns_last_pid():
    url = (
        "https://www.bbc.co.uk/iplayer/episodes/b006m8v5/series-4"
        "?seriesId=b03tzlwv&episodeId=b06nxnl4"
    )
    assert cli._normalise_pid(url) == "b06nxnl4"


def test_two_digit_handles_int_str_and_invalid():
    assert cli._two_digit(3) == "03"
    assert cli._two_digit("42") == "42"
    assert cli._two_digit("abc") == "00"
    assert cli._two_digit(None) == "00"


def test_sanitize_filename_component_strips_invalid_characters():
    raw = ' Episode: "Pilot"*?/ '
    assert cli._sanitize_filename_component(raw) == "Episode Pilot"


def test_format_plex_filename_uses_metadata():
    metadata = {
        "show_title": "My Show",
        "season_number": "1",
        "episode_number": "2",
        "episode_title": 'Pilot: "Arrival"?',
    }
    result = cli._format_plex_filename(metadata, "p0abc123", "mp4")
    assert result == "My Show - s01e02 - Pilot Arrival.mp4"


def test_format_plex_filename_falls_back_to_pid():
    result = cli._format_plex_filename({}, "p0def456", ".mkv")
    assert result == "P0DEF456 - s00e00 - P0DEF456.mkv"


def test_ensure_unique_path_appends_suffix(tmp_path):
    existing = tmp_path / "video.mp4"
    existing.write_bytes(b"")

    first_unique = cli._ensure_unique_path(tmp_path, "video.mp4")
    assert first_unique.name == "video (1).mp4"
    first_unique.write_bytes(b"")

    second_unique = cli._ensure_unique_path(tmp_path, "video.mp4")
    assert second_unique.name == "video (2).mp4"


def test_locate_download_directory_prefers_exact_match(temp_cwd):
    expected = temp_cwd / ".pget_iplayer-p0abc123-1a2b3c4d"
    expected.mkdir()
    found = cli._locate_download_directory("1a2b3c4d", "p0abc123")
    assert found == expected


def test_find_downloaded_video_returns_most_recent_candidate(tmp_path):
    old_video = tmp_path / "old.mp4"
    recent_dir = tmp_path / "sub"
    recent_dir.mkdir()
    recent_video = recent_dir / "recent.mkv"
    non_video = tmp_path / "ignored.txt"

    old_video.write_bytes(b"a" * 10)
    recent_video.write_bytes(b"a" * 20)
    non_video.write_text("nope")

    now = time.time()
    os.utime(old_video, (now - 100, now - 100))
    os.utime(recent_video, (now, now))

    found = cli._find_downloaded_video(tmp_path)
    assert found == recent_video


def test_extract_broadcast_date_parses_iso_timestamp():
    node = {"first_broadcast_date": "2024-06-10T20:00:00Z"}
    assert cli._extract_broadcast_date(node) == "20240610"


def test_extract_broadcast_date_returns_empty_on_failure():
    node = {"first_broadcast_date": "invalid"}
    assert cli._extract_broadcast_date(node) == ""


def test_safe_int_to_str_accepts_int_and_numeric_str():
    assert cli._safe_int_to_str(7) == "7"
    assert cli._safe_int_to_str("12") == "12"
    assert cli._safe_int_to_str("abc") == ""
    assert cli._safe_int_to_str(None) == ""


def test_get_bbc_episode_pids_returns_single_episode(monkeypatch):
    episode_pid = "b03tzlwv"
    responses = {
        f"https://www.bbc.co.uk/programmes/{episode_pid}.json": load_fixture("episode.json"),
    }
    install_fake_requests(monkeypatch, responses)

    result = cli.get_bbc_episode_pids(episode_pid)
    assert result == [episode_pid]


def test_get_bbc_episode_pids_series_uses_api(monkeypatch):
    series_pid = "b04vs4r9"
    base = f"https://www.bbc.co.uk/programmes/{series_pid}"
    responses = {
        f"{base}.json": load_fixture("series.json"),
        f"{base}/children.json?page=1": load_fixture("series_children_page_1.json"),
        f"{base}/children.json?page=2": load_fixture("series_children_page_2.json"),
    }
    install_fake_requests(monkeypatch, responses)

    result = cli.get_bbc_episode_pids(series_pid)
    assert result == ["b06nxnl4", "b06mty6m", "b06mtnf1"]


def test_get_bbc_episode_pids_brand_collects_from_series(monkeypatch):
    brand_pid = "b07xdmgk"
    brand_base = f"https://www.bbc.co.uk/programmes/{brand_pid}"
    series_pid = "b04vs4r9"
    series_base = f"https://www.bbc.co.uk/programmes/{series_pid}"
    responses = {
        f"{brand_base}.json": load_fixture("brand.json"),
        f"{brand_base}/children.json?page=1": load_fixture("brand_children.json"),
        f"{series_base}/children.json?page=1": load_fixture("series_children_page_1.json"),
        f"{series_base}/children.json?page=2": load_fixture("series_children_page_2.json"),
    }
    install_fake_requests(monkeypatch, responses)

    result = cli.get_bbc_episode_pids(brand_pid)
    assert result == ["b06nxnl4", "b06mty6m", "b06mtnf1"]


def test_get_bbc_episode_pids_falls_back_to_get_iplayer(monkeypatch):
    series_pid = "b04vs4r9"
    base = f"https://www.bbc.co.uk/programmes/{series_pid}"
    responses = {
        f"{base}.json": load_fixture("series.json"),
        f"{base}/children.json?page=1": {
            "children": {
                "page": 1,
                "total": 0,
                "limit": 50,
                "offset": 0,
                "programmes": [],
            }
        },
    }
    install_fake_requests(monkeypatch, responses)

    monkeypatch.setattr(
        cli,
        "_get_bbc_episode_pids_via_get_iplayer",
        lambda pid, timeout=120: ["via-fallback"],
    )

    result = cli.get_bbc_episode_pids(series_pid)
    assert result == ["via-fallback"]
