"""재생목록 파일 읽기/쓰기 (.m3u, .m3u8, .pls) — 다른 플레이어와 주고받을 수 있는 형식."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

PLAYLIST_EXTENSIONS = (".m3u8", ".m3u", ".pls")


def save_m3u(path: str | Path, items: list[str]) -> None:
    """UTF-8 확장 M3U 로 저장 (.m3u8 권장). 같은 폴더 아래 파일은 상대 경로로 쓴다."""
    base = os.path.dirname(os.path.abspath(path))
    lines = ["#EXTM3U"]
    for p in items:
        lines.append(f"#EXTINF:-1,{os.path.basename(p) if '://' not in p else p}")
        if "://" not in p:
            try:
                rel = os.path.relpath(p, base)
                p = rel if not rel.startswith("..") else p
            except ValueError:  # Windows: 다른 드라이브
                pass
        lines.append(p)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(entry: str, base: str) -> str:
    entry = entry.strip()
    if entry.lower().startswith("file://"):
        u = urlparse(entry)
        p = unquote(u.path)
        if os.name == "nt" and p.startswith("/") and len(p) > 2 and p[2] == ":":
            p = p[1:]
        return os.path.normpath(p)
    if "://" in entry:
        return entry
    entry = entry.replace("\\", os.sep) if os.sep == "/" else entry
    return os.path.normpath(entry if os.path.isabs(entry) else os.path.join(base, entry))


def load_playlist(path: str | Path) -> list[str]:
    """m3u/m3u8/pls 를 읽어 파일 경로(또는 URL) 목록을 돌려준다."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    base = os.path.dirname(os.path.abspath(path))
    out: list[str] = []
    if str(path).lower().endswith(".pls"):
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower().startswith("file") and value.strip():
                out.append(_resolve(value, base))
    else:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(_resolve(line, base))
    return out
