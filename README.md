# Rumi

mpv 엔진 기반의 설치형 동영상 플레이어입니다. **중복 동영상 정리** 기능이 들어 있습니다.
Windows, macOS, Linux에서 동작합니다 (Python + Qt(PySide6) + libmpv).

## 주요 기능

### 플레이어
- MKV, MP4, AVI, MOV, WMV, FLV, TS, WebM 등 mpv가 지원하는 거의 모든 형식, 하드웨어 가속 디코딩
- 재생목록: 폴더째 열기, 끌어다 놓기, 순서 바꾸기, 이름순(자연) 정렬, 순서대로/전체 반복/한 개 반복/무작위
- **이어보기**: 10초 이상 본 파일은 마지막 위치부터 다시 재생 (Home 키로 처음부터)
- 자막: 같은 이름의 SRT/SMI/ASS 자동 불러오기, 인코딩 자동 감지, 트랙 선택, 싱크·크기 조절
  (블랙박스 영상의 G-센서/GPS 데이터 트랙은 자막으로 띄우지 않음)
- 하드웨어 가속: 기본은 꺼짐(mpv 기본값과 같음, 가장 안정적). **화면 → 하드웨어 가속**에서 켤 수 있음
- 화면이 깨지거나 검게 나오면 **화면 → 호환 모드**, 문제 신고 시 **도움말 → 진단 정보 복사**
- 오디오 트랙 선택, 배속(0.1~4배), A-B 구간 반복, 프레임 단위 이동, 화면 비율, 스크린샷
- 전체화면(컨트롤 자동 숨김), 항상 위, 최근 파일, URL 스트림 열기

### 라이브러리와 중복 정리
1. **라이브러리 → 폴더 추가**로 동영상 폴더를 등록하면 하위 폴더까지 스캔합니다.
2. **중복 정리 → 중복 찾기**: 파일 이름이 달라도 *내용이 완전히 같은* 파일을 찾습니다.
   크기 → 부분 해시 → 전체 해시(BLAKE2b) 순서로 비교하므로, 크기가 같은 파일만 끝까지 읽습니다.
3. 그룹마다 가장 오래된 파일을 남기도록 자동 추천하고, 나머지는 체크됩니다. 체크는 바꿀 수 있고, 그룹마다 최소 한 개는 남겨야 합니다.
4. 정리 방법 고르기:
   - **표시만 하기**: 파일은 그대로 두고 정리함에 올립니다.
   - **격리 폴더로 이동**: 라이브러리 폴더 안 `.rumi_quarantine/`로 원래 폴더 구조를 유지한 채 옮깁니다.
5. **정리함**에서 **복원**하거나, 충분히 확인한 뒤 **영구 삭제**합니다.
   삭제 직전에 *남겨 둘 사본이 실제로 있고 내용이 같은지* 파일을 다시 읽어 확인합니다. 마지막 남은 사본은 절대 삭제하지 않습니다.
   확인 창에서 체크박스로 한 번 더 동의해야 삭제됩니다.

## 단축키

| 키 | 동작 | 키 | 동작 |
|---|---|---|---|
| Space / 클릭 | 재생·일시정지 | Enter, F, 더블클릭 | 전체화면 |
| ← / → | 5초 이동 | Ctrl + ← / → | 30초 이동 |
| , / . | 프레임 이동 | Home | 처음으로 |
| PgUp / PgDn | 이전·다음 파일 | ↑ / ↓, 휠 | 볼륨 |
| M | 음소거 | [ / ] / Backspace | 배속 조절·초기화 |
| L | A-B 구간 반복 | S | 스크린샷(바탕화면) |
| V | 자막 보이기 | Z / X | 자막 싱크 ±0.1초 |
| F9 | 재생목록 | Ctrl + L / Ctrl + D | 라이브러리 / 중복 정리 |

## 개발 환경에서 실행

```bash
# 1) libmpv 설치
#   Ubuntu/Debian : sudo apt install libmpv-dev   (패키지 이름이 libmpv2 인 배포판도 있음)
#   macOS         : brew install mpv
#   Windows       : https://github.com/shinchiro/mpv-winbuild-cmake/releases 에서
#                   mpv-dev-x86_64-*.7z 를 받아 libmpv-2.dll 을 PATH 또는 프로젝트 폴더에 둡니다
# 2) 실행
pip install -e ".[dev]"
python -m rumi [동영상 파일 또는 폴더...]
pytest            # 라이브러리/중복 정리 로직 테스트
```

## 설치 파일 만들기

| OS | 명령 | 결과 |
|---|---|---|
| Windows | `python packaging/build.py` 다음 `iscc packaging\windows\rumi.iss` | `dist/RumiSetup-0.1.2.exe` (시작 메뉴, 파일 연결 선택) |
| macOS | `python packaging/build.py` | `dist/Rumi.app` |
| Linux | `python packaging/build.py` | `dist/Rumi/Rumi` |

Windows에서는 `libmpv-2.dll`을 `packaging/windows/` 아래 아무 곳에 두거나, `RUMI_LIBMPV` 환경 변수로 경로를 지정하세요.

각 OS에서 직접 빌드하지 않아도 됩니다. GitHub의 **Actions → build → Run workflow**를 실행하면(또는 `v0.1.2` 같은 태그를 푸시하면) 세 OS용 설치 파일이 한 번에 만들어집니다.

## 데이터 위치

라이브러리 DB(`library.sqlite3`)와 이어보기 위치는 OS별 앱 데이터 폴더에 저장됩니다.
- Windows `%APPDATA%\Rumi\Rumi`
- macOS `~/Library/Application Support/Rumi/Rumi`
- Linux `~/.local/share/Rumi/Rumi`

## 구조

```
rumi/
  library/        GUI와 무관한 핵심 로직 (테스트 대상)
    db.py         SQLite 저장소 (동영상, 폴더, 이어보기)
    scanner.py    폴더 스캔
    duplicates.py 중복 탐지 (크기 → 부분 해시 → 전체 해시)
    organizer.py  표시 / 격리 / 복원 / 안전한 영구 삭제
  ui/
    mpv_widget.py     libmpv OpenGL 렌더 위젯
    main_window.py    플레이어 창, 메뉴, 단축키
    controls.py       탐색 바·버튼·볼륨
    playlist.py       재생목록
    library_window.py 라이브러리 / 중복 정리 / 정리함
packaging/        PyInstaller, Inno Setup 설정
```
