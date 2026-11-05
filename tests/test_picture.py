import os

from rumi import picture
from rumi.media_tools import AudioSettings, build_audio_filter


def test_quality_presets_and_shaders_exist():
    assert picture.quality_options("default") == {}
    assert picture.quality_options("hq")["scale"] == "ewa_lanczossharp"
    assert picture.shader_paths("hq") == []
    sharp = picture.shader_paths("sharp")
    anime = picture.shader_paths("anime")
    assert len(sharp) == 1 and len(anime) == len(picture.ANIME4K_CHAIN)
    for path in sharp + anime:  # 설치 파일에 실제로 들어가야 하는 셰이더
        assert os.path.isfile(path), path
        assert "//!HOOK" in open(path, encoding="utf-8").read()


def test_hdr_options():
    assert picture.hdr_options("auto") == {}
    assert picture.hdr_options("dynamic")["hdr-compute-peak"] == "yes"
    assert picture.is_hdr({"gamma": "pq"}) and picture.is_hdr({"gamma": "HLG"})
    assert not picture.is_hdr({"gamma": "bt.1886"}) and not picture.is_hdr(None)


def test_smooth_options():
    on = picture.smooth_options(True, 143.856)
    assert on["video-sync"] == "display-resample" and on["interpolation"] is True
    assert on["display-fps-override"] == 143.856
    assert "display-fps-override" not in picture.smooth_options(True, None)
    assert picture.smooth_options(False) == {"video-sync": "audio", "interpolation": False}


def test_touched_options_cover_presets():
    touched = set(picture.TOUCHED_OPTIONS)
    assert set(picture.HQ_OPTIONS) <= touched
    for opts in picture.HDR_OPTIONS.values():
        assert set(opts) <= touched


def test_dialogue_filter_first_and_stereo():
    af = build_audio_filter(AudioSettings(dialogue=True, bass=3))
    first = af.split(",lavfi")[0]
    assert "dialoguenhance" in first and first.rstrip("]").endswith("aformat=channel_layouts=stereo")
    assert "bass=" in af
    assert not AudioSettings(dialogue=True).is_default()
    assert "dialoguenhance" not in build_audio_filter(AudioSettings())


def test_effective_quality_limits_anime4k_to_1080p():
    assert picture.effective_quality("anime", 1080) == "anime"
    assert picture.effective_quality("anime", 2160) == "sharp"
    assert picture.effective_quality("anime", None) == "anime"
    assert picture.effective_quality("hq", 2160) == "hq"


def test_is_overloaded():
    assert not picture.is_overloaded(0, 60, 20)
    assert picture.is_overloaded(10, 60, 20)       # 프레임 1/6 을 버림
    assert not picture.is_overloaded(3, 5, 0)      # 표본이 너무 적음
    assert picture.is_overloaded(0, 60, 400)       # 화면이 0.4초 멈춤


def test_step_down_order_and_end():
    state, steps = (True, "anime", "dynamic"), []
    while (r := picture.step_down(*state)) is not None:
        state, msg = r
        steps.append(state)
    assert steps == [(False, "anime", "dynamic"), (False, "hq", "dynamic"),
                     (False, "default", "dynamic"), (False, "default", "auto")]
    assert not picture.is_heavy(*state)
