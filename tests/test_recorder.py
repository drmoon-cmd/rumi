from rumi.recorder import record_args


def test_record_args_copies_without_reencoding():
    args = record_args("https://example.com/trailer.m3u8", "out.mkv")
    assert args[args.index("-i") + 1] == "https://example.com/trailer.m3u8"
    assert args[args.index("-c") + 1] == "copy"
    assert args[-1] == "out.mkv"
    assert "-rtsp_transport" not in args


def test_record_args_rtsp_uses_tcp():
    args = record_args("rtsp://192.168.0.10/stream1", "cam.mkv")
    assert args[args.index("-rtsp_transport") + 1] == "tcp"
    assert args.index("-rtsp_transport") < args.index("-i")
