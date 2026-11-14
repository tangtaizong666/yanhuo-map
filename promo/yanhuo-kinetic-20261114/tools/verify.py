import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MP4 = ROOT / "yanhuo-kinetic-38s-1080p60.mp4"


def main() -> None:
    if not MP4.exists():
        raise FileNotFoundError(MP4)

    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,pix_fmt,color_range,color_space,color_transfer,color_primaries,sample_rate,channels",
                "-of", "json", str(MP4),
            ],
            text=True,
        )
    )
    v_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")

    # Full decode check + frame count
    dec = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(MP4), "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    assert dec.stderr.strip() == "", f"FFmpeg decode errors: {dec.stderr}"

    fc_out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "default=nokey=1:noprint_wrappers=1", str(MP4),
        ],
        text=True,
    ).strip()
    frames = int(fc_out)

    ebur = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(MP4), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stderr
    i_match = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", ebur)
    tp_match = re.findall(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", ebur)
    lufs = float(i_match[-1]) if i_match else None
    tp = float(tp_match[-1]) if tp_match else None

    color_tuple = (
        v_stream.get("pix_fmt"),
        v_stream.get("color_range"),
        v_stream.get("color_space"),
        v_stream.get("color_transfer"),
        v_stream.get("color_primaries"),
    )
    assert v_stream["width"] == 1920 and v_stream["height"] == 1080, v_stream
    assert v_stream["r_frame_rate"] == "60/1", v_stream
    assert frames == 2280, f"Expected 2280 frames, got {frames}"
    assert color_tuple == ("yuv420p", "tv", "bt709", "bt709", "bt709"), f"Bad color VUI: {color_tuple}"
    assert int(a_stream["sample_rate"]) == 48000 and int(a_stream["channels"]) == 2, a_stream

    sha256 = hashlib.sha256(MP4.read_bytes()).hexdigest()
    report = {
        "file": MP4.name,
        "bytes": MP4.stat().st_size,
        "sha256": sha256,
        "duration_seconds": round(float(probe["format"]["duration"]), 3),
        "width": v_stream["width"],
        "height": v_stream["height"],
        "fps": v_stream["r_frame_rate"],
        "decoded_video_frames": frames,
        "video_codec": v_stream["codec_name"],
        "color_parameters": list(color_tuple),
        "audio_codec": a_stream["codec_name"],
        "audio_sample_rate": int(a_stream["sample_rate"]),
        "audio_channels": int(a_stream["channels"]),
        "integrated_loudness_lufs": lufs,
        "true_peak_dbtp": tp,
        "decode_check": "passed; all 2280 frames at 60fps and 48kHz stereo audio decoded with zero FFmpeg errors",
    }
    (ROOT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
