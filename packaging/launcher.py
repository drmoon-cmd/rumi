"""PyInstaller 진입점 (패키지 상대 import 를 쓰기 위해 별도 파일로 둔다)."""

import sys

from rumi.__main__ import main

sys.exit(main())
