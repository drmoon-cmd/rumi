"""로컬 전용 모드: Rumi 가 어떤 경우에도 인터넷에 접속하지 않게 한다 (기본으로 켜져 있다).

Rumi 자체에는 통계 전송·업데이트 확인 같은 통신이 없다. 남는 경로는 재생 엔진(mpv)과 변환 도구(ffmpeg)가
'주소'를 열 때뿐인데, 사용자가 주소를 직접 넣지 않아도 생길 수 있다:
- 겉은 로컬 파일이지만 안에 인터넷 주소가 적힌 파일 (재생목록 .m3u/.pls, HLS 목록 .m3u8,
  확장자만 .mp4 로 바꾼 목록 파일 등). 열면 재생 엔진이 그 주소로 접속한다.
그래서 두 겹으로 막는다:
1) Rumi 가 인터넷 주소를 재생 엔진·변환 도구에 넘기지 않는다 (is_remote 로 거른다)
2) 재생 엔진·변환 도구에 '로컬 파일만 읽기'를 건다 (파일 안에 숨은 주소도 열지 못한다)
"""

from __future__ import annotations

import re

# 재생 엔진·변환 도구가 읽어도 되는 것: 로컬 파일과, 파일 안의 일부를 가리키는 방식들 (네트워크 없음)
MPV_PROTOCOLS = "file,crypto,data,subfile"
FFMPEG_PROTOCOLS = "file,crypto,data,subfile,pipe"   # pipe: 결과를 Rumi 로 돌려주는 통로
MPV_LAVF_GUARD = f"protocol_whitelist=[{MPV_PROTOCOLS}]"

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_local_only = True


def local_only() -> bool:
    return _local_only


def set_local_only(on: bool) -> None:
    global _local_only
    _local_only = bool(on)


def is_remote(path: str) -> bool:
    """인터넷·네트워크 주소인가 (http://, rtsp://, smb:// …). 로컬 파일 경로와 file:// 은 아니다."""
    if not path or not _SCHEME.match(path):
        return False
    return not path.lower().startswith("file://")


def blocked(path: str) -> bool:
    """로컬 전용 모드에서 열면 안 되는 주소인가."""
    return _local_only and is_remote(path)


def ffmpeg_input_guard() -> list[str]:
    """ffmpeg 입력 앞에 붙이는 옵션: 로컬 파일만 읽게 한다. 로컬 파일만 다루는 작업에는 모드와 상관없이 붙인다."""
    return ["-protocol_whitelist", FFMPEG_PROTOCOLS]
