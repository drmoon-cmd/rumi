"""블랙박스 앞/뒤 카메라 파일 짝 찾기."""

from __future__ import annotations

import os
import re

# 파일 이름 안의 앞/뒤 표시 (대소문자 무시). 왼쪽이 앞, 오른쪽이 뒤.
_TOKEN_PAIRS = [("F", "R"), ("FRONT", "REAR"), ("FR", "RR"), ("전방", "후방"), ("앞", "뒤")]
_FOLDER_PAIRS = [("front", "rear"), ("전방", "후방"), ("f", "r")]
_SPLIT = re.compile(r"([_\-.\s()\[\]])")


def _swap_tokens(stem: str) -> list[str]:
    """이름 조각 중 앞/뒤 표시를 반대로 바꾼 후보 이름들."""
    parts = _SPLIT.split(stem)
    out = []
    for i, part in enumerate(parts):
        up = part.upper()
        for front, rear in _TOKEN_PAIRS:
            for a, b in ((front, rear), (rear, front)):
                if up == a.upper():
                    swapped = b if part.isupper() or not part.isalpha() else b.lower()
                    out.append("".join(parts[:i] + [swapped] + parts[i + 1:]))
    # 이름 끝에 붙은 한 글자: 20260824_074217F → 20260824_074217R
    m = re.match(r"^(.*\d)([FRfr])$", stem)
    if m:
        letter = m.group(2)
        other = {"F": "R", "R": "F", "f": "r", "r": "f"}[letter]
        out.append(m.group(1) + other)
    return out


def _find_case_insensitive(folder: str, name: str) -> str | None:
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    lower = name.lower()
    for e in entries:
        if e.lower() == lower:
            return os.path.join(folder, e)
    return None


def find_partner(path: str) -> str | None:
    """앞 카메라 파일이면 뒤 카메라 파일을, 뒤면 앞을 찾는다. 없으면 None."""
    folder, filename = os.path.split(path)
    stem, ext = os.path.splitext(filename)
    names = [s + ext for s in _swap_tokens(stem)]

    folders = [folder]
    parent, base = os.path.split(folder)
    for a, b in _FOLDER_PAIRS:
        for x, y in ((a, b), (b, a)):
            if base.lower() == x.lower():
                other = _find_case_insensitive(parent, y)
                if other:
                    folders.append(other)
    for d in folders:
        for n in names + ([filename] if d != folder else []):
            hit = _find_case_insensitive(d, n)
            if hit and os.path.normcase(hit) != os.path.normcase(path):
                return hit
    return None
