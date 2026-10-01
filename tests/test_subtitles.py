from rumi.subtitles import looks_like_sensor_data


def test_dashcam_gsensor_line_is_data():
    assert looks_like_sensor_data("gsensori,4,512,128,003,028;CAR,0,0,0,0.0,0,0,0,0,0,0,0,0")
    assert looks_like_sensor_data("$GPRMC,074217,A,3729.1234,N,12652.5678,E,0.0,0.0,240826,,")


def test_normal_subtitles_are_not_data():
    assert not looks_like_sensor_data("")
    assert not looks_like_sensor_data("안녕하세요, 반갑습니다.")
    assert not looks_like_sensor_data("1, 2, 3, 4, 5, 6, 7 하나 둘 셋")
    assert not looks_like_sensor_data("Hello,\nworld")
    assert not looks_like_sensor_data("2024,10,01")
