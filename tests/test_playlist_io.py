import os

from rumi.playlist_io import load_playlist, save_m3u


def test_m3u_roundtrip_relative_and_absolute(tmp_path):
    (tmp_path / "sub").mkdir()
    a = str(tmp_path / "sub" / "앞 카메라.mp4")
    b = "/elsewhere/movie.mkv"
    url = "https://example.com/live.m3u8"
    f = tmp_path / "list.m3u8"
    save_m3u(f, [a, b, url])
    text = f.read_text(encoding="utf-8")
    assert "sub/앞 카메라.mp4" in text.replace("\\", "/")      # 같은 폴더 아래는 상대 경로
    assert load_playlist(f) == [os.path.normpath(a), os.path.normpath(b), url]


def test_load_cp949_m3u_and_pls(tmp_path):
    m3u = tmp_path / "old.m3u"
    m3u.write_bytes("#EXTM3U\n#EXTINF:12,제목\n영상.avi\n".encode("cp949"))
    assert load_playlist(m3u) == [os.path.normpath(str(tmp_path / "영상.avi"))]
    pls = tmp_path / "a.pls"
    pls.write_text("[playlist]\nFile1=one.mp4\nTitle1=x\nFile2=file:///tmp/two%20b.mp4\nNumberOfEntries=2\n")
    assert load_playlist(pls) == [os.path.normpath(str(tmp_path / "one.mp4")), os.path.normpath("/tmp/two b.mp4")]
