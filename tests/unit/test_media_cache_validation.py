"""Regression tests for media cache content validation."""

from pathlib import Path

import pytest

from agent.services import media_cache


JPEG = b"\xff\xd8\xff\xe0" + b"jpeg payload"
HTML = b"<!doctype html><html><body>login required</body></html>"
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00" + b"mp4 payload"


class _Response:
    def __init__(self, body, content_type="image/jpeg", status=200):
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.status = status

    async def read(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, _url):
        return self.response


def test_cached_html_is_not_reported_as_an_image(monkeypatch, tmp_path):
    monkeypatch.setattr(media_cache, "MEDIA_CACHE_DIR", tmp_path)
    bad = tmp_path / "media-1.jpg"
    bad.write_bytes(HTML)

    assert media_cache.get_cached_image_path("media-1") is None
    assert not media_cache.is_valid_media_file(bad)


def test_cached_image_requires_an_image_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(media_cache, "MEDIA_CACHE_DIR", tmp_path)
    good = tmp_path / "media-2.jpg"
    good.write_bytes(JPEG)

    assert media_cache.get_cached_image_path("media-2") == good
    assert media_cache.is_valid_media_file(good)


@pytest.mark.asyncio
async def test_download_rejects_html_even_when_http_status_is_200(monkeypatch, tmp_path):
    response = _Response(HTML, content_type="text/html")
    monkeypatch.setattr(media_cache.aiohttp, "ClientSession", lambda **_kwargs: _Session(response))
    dest = tmp_path / "media-3.jpg"

    assert not await media_cache.download_to_file(
        "https://flow-content.google/image/media-3", dest
    )
    assert not dest.exists()


@pytest.mark.asyncio
async def test_download_writes_valid_image_atomically(monkeypatch, tmp_path):
    response = _Response(JPEG, content_type="image/jpeg")
    monkeypatch.setattr(media_cache.aiohttp, "ClientSession", lambda **_kwargs: _Session(response))
    dest = tmp_path / "media-4.jpg"

    assert await media_cache.download_to_file(
        "https://flow-content.google/image/media-4", dest
    )
    assert dest.read_bytes() == JPEG
    assert media_cache.is_valid_media_file(dest)


@pytest.mark.asyncio
async def test_download_accepts_mp4_signature(monkeypatch, tmp_path):
    response = _Response(MP4, content_type="video/mp4")
    monkeypatch.setattr(media_cache.aiohttp, "ClientSession", lambda **_kwargs: _Session(response))
    dest = tmp_path / "media-5.mp4"

    assert await media_cache.download_to_file(
        "https://flow-content.google/video/media-5", dest
    )
    assert media_cache.is_valid_media_file(dest)
