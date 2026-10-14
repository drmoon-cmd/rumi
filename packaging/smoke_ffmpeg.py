"""빌드 점검: 설치 파일에 들어갈 ffmpeg 로 '동영상 합치기'와 '녹화'가 실제로 되는지 확인한다.

.ts 조각을 만들어 하나로 합치고(mp4·mkv), 녹화와 같은 명령으로 옮겨 담은 뒤 결과가 끝까지
재생(디코딩)되는지 본다. 하나라도 실패하면 0 이 아닌 코드로 끝나 빌드를 멈춘다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Windows 빌드 콘솔(cp1252)에서도 한글 파일 이름·메시지를 출력할 수 있게
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

from rumi.merge import build_merge  # noqa: E402
from rumi.recorder import record_args  # noqa: E402
from rumi.tools import find_ffmpeg  # noqa: E402


def run(args: list[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"실패 (코드 {r.returncode}): {' '.join(args)}\n{r.stderr[-1500:]}")


def check_decodes(ffmpeg: str, path: str) -> None:
    if not os.path.exists(path) or os.path.getsize(path) < 10_000:
        raise SystemExit(f"결과 파일이 없거나 너무 작습니다: {path}")
    run([ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"])
    print(f"  OK  {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")


def main() -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("ffmpeg 를 찾지 못했습니다")
    print("ffmpeg:", ffmpeg)
    with tempfile.TemporaryDirectory(prefix="rumi_smoke_") as d:
        pieces = []
        for i in range(1, 4):
            p = os.path.join(d, f"조각_{i}.ts")
            run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
                 "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-f", "mpegts", p])
            pieces.append(p)
        for ext in (".mp4", ".mkv"):
            out = os.path.join(d, "합침" + ext)
            list_file = os.path.join(d, "list.ffconcat")
            text, args = build_merge(pieces, list_file, out, ffmpeg)
            with open(list_file, "w", encoding="utf-8") as f:
                f.write(text)
            run(args)
            check_decodes(ffmpeg, out)
        out = os.path.join(d, "녹화.mkv")
        run([ffmpeg] + record_args(pieces[0], out))
        check_decodes(ffmpeg, out)
    print("ffmpeg 점검 통과")


if __name__ == "__main__":
    main()
