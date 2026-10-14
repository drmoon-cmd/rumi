import os
import re

from rumi.merge import build_merge, default_output, expand_paths


def _key(p):  # rumi.ui.util.natural_key 와 같은 규칙 (GUI 모듈은 CI 에서 불러오지 않는다)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.basename(p))]


def test_expand_paths_sorts_folder_naturally_and_skips_non_video(tmp_path):
    for name in ("seg10.ts", "seg2.ts", "seg1.ts", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "seg3.ts").write_bytes(b"x")
    got = [os.path.basename(p) for p in expand_paths([str(tmp_path)], _key)]
    assert got == ["seg1.ts", "seg2.ts", "seg10.ts"]


def test_expand_paths_drops_duplicates(tmp_path):
    f = tmp_path / "a.ts"
    f.write_bytes(b"x")
    assert expand_paths([str(f), str(f), str(tmp_path)], _key) == [str(f)]


def test_default_output_strips_trailing_number():
    assert default_output(["/v/clip_001.ts"]) == os.path.join("/v", "clip_합침.mp4")
    assert default_output(["/v/123.ts"]) == os.path.join("/v", "123_합침.mp4")


def test_build_merge_copies_in_order_and_escapes_quotes():
    text, args = build_merge(["/v/it's 1.ts", "/v/b.ts"], "list.ffconcat", "out.mp4", "ffmpeg")
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1] == r"file '/v/it'\''s 1.ts'"
    assert lines[2] == "file '/v/b.ts'"
    assert args[args.index("-c") + 1] == "copy"
    assert "+faststart" in args
    assert args[-1] == "out.mp4"
    _, mkv_args = build_merge(["a.ts", "b.ts"], "l", "out.mkv")
    assert "-movflags" not in mkv_args
