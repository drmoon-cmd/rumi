"""로컬 전용 모드: 인터넷 주소를 가려내고, ffmpeg 가 로컬 파일만 읽게 한다."""

import http.server
import os
import threading

import pytest

from rumi import netguard
from rumi.library import fingerprint as fp
from rumi.media_tools import build_thumbnail_args
from rumi.tools import find_ffmpeg


def test_is_remote():
    for url in ("http://a/b.mp4", "https://a/b", "rtsp://cam/1", "smb://nas/x.mkv", "ftp://h/x", "HTTP://A/B",
                "udp://239.0.0.1:1234", "mms://x/y"):
        assert netguard.is_remote(url), url
    for path in ("/home/u/영화.mp4", r"C:\Videos\a.mp4", r"\\nas\share\a.mp4", "file:///home/u/a.mp4",
                 "a b.mp4", "", "이름: 제목 - 1.mp4", "C:/x/y.mp4"):
        assert not netguard.is_remote(path), path


def test_blocked_follows_mode():
    try:
        netguard.set_local_only(True)
        assert netguard.local_only() and netguard.blocked("http://a/b") and not netguard.blocked("/a/b.mp4")
        netguard.set_local_only(False)
        assert not netguard.blocked("http://a/b")
    finally:
        netguard.set_local_only(True)


def test_ffmpeg_commands_carry_the_guard():
    guard = netguard.ffmpeg_input_guard()
    assert guard[0] == "-protocol_whitelist" and "http" not in guard[1] and "tcp" not in guard[1]
    args = build_thumbnail_args("ffmpeg", "/a.mp4", 1.0)
    assert args.index("-protocol_whitelist") < args.index("-i")


@pytest.mark.skipif(not find_ffmpeg(), reason="ffmpeg 없음")
def test_scanning_a_file_that_points_to_the_internet_makes_no_connection(tmp_path):
    """겉은 .mp4 인데 안에 주소가 적힌 파일 (HLS 목록): 지문을 만들 때 그 주소로 접속하면 안 된다."""
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        fake = tmp_path / "movie.mp4"
        fake.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n#EXTINF:5.0,\n"
                        f"http://127.0.0.1:{port}/leak.ts\n#EXT-X-ENDLIST\n")
        info = fp.probe(str(fake))
        fp.fingerprint(str(fake), info.duration or 5.0)
        fp._grab_one(find_ffmpeg(), f"http://127.0.0.1:{port}/direct.mp4", 0.0)   # 주소를 직접 줘도 막힌다
    finally:
        server.shutdown()
    assert hits == []
    assert os.path.exists(fake)
