"""자막 관련 판별 도우미 (GUI 비의존)."""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"[,;:|]")
_DATA_CHARS = set("0123456789.,;:|-+")


def looks_like_sensor_data(text: str) -> bool:
    """블랙박스가 자막 트랙에 넣어 두는 G-센서/GPS 데이터인지 판별.

    예: 'gsensori,4,512,128,003,028;CAR,0,0,0,0.0,0,0,0,0,0,0,0,0'
    공백 없는 한 줄에 구분자가 많고 대부분이 숫자인 경우를 데이터로 본다.
    """
    t = text.strip()
    if not t or "\n" in t or " " in t:
        return False
    if len(_SEPARATORS.findall(t)) < 6:
        return False
    return sum(c in _DATA_CHARS for c in t) / len(t) >= 0.6
