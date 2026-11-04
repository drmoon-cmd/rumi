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
