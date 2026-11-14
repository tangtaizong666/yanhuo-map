"""Strict broadcast & decode verification for Film 1: yanhuo-matchcut-48s-1080p60.mp4."""

from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parents[1]
mp4 = root / "yanhuo-matchcut-48s-1080p60.mp4"


def run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"Command failed: {args}\n{r.stderr[-4000:]}"
    return r


meta = json.loads(
    run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4)]).stdout
)
v_streams = [s for s in meta["streams"] if s["codec_type"] == "video"]
a_streams = [s for s in meta["streams"] if s["codec_type"] == "audio"]
assert len(v_streams) == 1 and len(a_streams) == 1, (len(v_streams), len(a_streams))

v, a = v_streams[0], a_streams[0]
assert (v["codec_name"], v["width"], v["height"], v["r_frame_rate"], int(v["nb_frames"])) == (
    "h264",
    1920,
    1080,
    "60/1",
    2880,
), v
assert abs(float(meta["format"]["duration"]) - 48.0) < 0.06, meta["format"]["duration"]
assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2), a

frames = json.loads(
    run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=pix_fmt,color_range,color_space,color_primaries,color_transfer",
            "-of",
            "json",
            str(mp4),
        ]
    ).stdout
)["frames"]
assert len(frames) == 2880, len(frames)
colors = {
    tuple(f.get(k) for k in ["pix_fmt", "color_range", "color_space", "color_primaries", "color_transfer"])
    for f in frames
}
assert colors == {("yuv420p", "tv", "bt709", "bt709", "bt709")}, colors

dec = run(["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(mp4), "-f", "null", "-"])
assert not dec.stderr.strip(), dec.stderr

loud_err = run(
    [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(mp4),
        "-vn",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
).stderr
loud = json.JSONDecoder().raw_decode(loud_err[loud_err.rfind("{") :])[0]
assert float(loud["input_tp"]) <= -1.0, loud

report = {
    "file": mp4.name,
    "bytes": mp4.stat().st_size,
    "sha256": hashlib.sha256(mp4.read_bytes()).hexdigest(),
    "duration_seconds": float(meta["format"]["duration"]),
    "width": v["width"],
    "height": v["height"],
    "fps": v["r_frame_rate"],
    "decoded_video_frames": len(frames),
    "video_codec": v["codec_name"],
    "color_parameters": list(next(iter(colors))),
    "audio_codec": a["codec_name"],
    "audio_sample_rate": int(a["sample_rate"]),
    "audio_channels": a["channels"],
    "integrated_loudness_lufs": float(loud["input_i"]),
    "true_peak_dbtp": float(loud["input_tp"]),
    "decode_check": "passed; all 2880 frames at 60fps and 48kHz stereo audio decoded with zero FFmpeg errors",
}
(root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
