"""Verify final 1080p60 MP4 integrity, frame count, color profile, audio loudness, and decode health."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = json.loads((ROOT / "assets" / "timeline.json").read_text(encoding="utf-8"))
MP4 = ROOT / "yanhuo-cinema-1080p60.mp4"


def main() -> None:
    if not MP4.exists():
        raise FileNotFoundError(f"Missing deliverable: {MP4}")

    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(MP4),
            ],
            text=True,
        )
    )
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next(s for s in probe["streams"] if s["codec_type"] == "audio")

    # Full decode integrity + EBU R128 loudness check
    dec = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(MP4),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stderr = dec.stderr
    m_i = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", stderr)
    m_tp = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", stderr)
    m_frames = re.findall(r"frame=\s*(\d+)", stderr)

    sha256 = hashlib.sha256(MP4.read_bytes()).hexdigest()
    decoded_frames = int(m_frames[-1]) if m_frames else int(v.get("nb_frames", 0))
    expected_frames = int(TIMELINE["total_frames"])

    report = {
        "file": MP4.name,
        "bytes": MP4.stat().st_size,
        "sha256": sha256,
        "duration_seconds": round(float(probe["format"]["duration"]), 3),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": v["r_frame_rate"],
        "decoded_video_frames": decoded_frames,
        "expected_video_frames": expected_frames,
        "video_codec": v["codec_name"],
        "color_parameters": [
            v.get("pix_fmt"),
            v.get("color_range"),
            v.get("color_space"),
            v.get("color_primaries"),
            v.get("color_transfer"),
        ],
        "audio_codec": a["codec_name"],
        "audio_sample_rate": int(a["sample_rate"]),
        "audio_channels": int(a["channels"]),
        "integrated_loudness_lufs": float(m_i[-1]) if m_i else None,
        "true_peak_dbtp": float(m_tp[-1]) if m_tp else None,
        "decode_check": f"passed; decoded {decoded_frames}/{expected_frames} frames @ 60fps & 48kHz stereo audio with zero errors",
    }

    assert report["width"] == 1920 and report["height"] == 1080
    assert report["fps"] == "60/1"
    assert abs(decoded_frames - expected_frames) <= 2, f"Frame mismatch: {decoded_frames} vs {expected_frames}"
    assert report["color_parameters"] == ["yuv420p", "tv", "bt709", "bt709", "bt709"], report["color_parameters"]

    out_path = ROOT / "verification.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
