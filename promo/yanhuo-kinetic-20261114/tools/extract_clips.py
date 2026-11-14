import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT.parents[1] / ".runtime" / "promo-stock"
CLIPS_DIR = ROOT / "work" / "clips"

# Every clip here is from verified Pexels-licensed stock in .runtime/promo-stock
# Each tuple: (clip_id, filename, start_sec, duration_sec, speed_factor, width, height, extra_vf)
SPECS = [
    # Act I craving hook & deadpan stop (generous frame headroom so zero clamping)
    ("c01_wok_fire", "wok-5336203.mp4", 4.50, 1.30, 1.25, 1920, 1080, "eq=contrast=1.10:saturation=1.22:brightness=0.02"),
    ("c02_sauce_toss", "sauce-2620043.mp4", 7.05, 1.35, 1.30, 1920, 1080, "eq=contrast=1.08:saturation=1.18"),
    ("c03_noodles_lift", "noodles-4223987.mp4", 3.20, 1.35, 1.45, 1920, 1080, "eq=contrast=1.08:saturation=1.16"),
    ("c04_sprint_steps", "gardenwalk-5177680.mp4", 5.00, 2.30, 2.20, 1920, 1080, "eq=contrast=1.08:saturation=1.10"),
    ("c05_empty_gate", "gardenwalk-5177680.mp4", 0.30, 2.15, 1.00, 1920, 1080, "eq=contrast=1.05:saturation=0.72:brightness=-0.05"),
    ("c06_phone_walk", "phone-5665074.mp4", 1.50, 2.60, 1.20, 1920, 1080, "eq=contrast=1.06:saturation=1.08"),
    # Act II collage windows & split-screen wok
    ("c07_collage_noodles", "noodles-4223987.mp4", 5.60, 5.40, 1.05, 960, 600, "eq=contrast=1.08:saturation=1.18"),
    ("c08_collage_lemon", "lemon-9001899.mp4", 3.50, 5.40, 1.08, 960, 600, "eq=contrast=1.08:saturation=1.20"),
    ("c09_collage_bite", "plate-5848630.mp4", 0.02, 1.55, 0.31, 960, 600, "eq=contrast=1.06:saturation=1.16"),
    ("c10_wok_split", "wok-5336203.mp4", 14.10, 3.40, 0.76, 1280, 1080, "eq=contrast=1.12:saturation=1.24"),
    # Act III rapid sensory payoff & finale bokeh
    ("c11_payoff_sauce", "sauce-2620043.mp4", 18.90, 1.65, 1.25, 1920, 1080, "eq=contrast=1.10:saturation=1.20"),
    ("c12_payoff_wok", "wok-5336203.mp4", 7.70, 1.65, 1.25, 1920, 1080, "eq=contrast=1.10:saturation=1.22"),
    ("c13_payoff_lemon", "lemon-9001899.mp4", 4.90, 1.65, 1.25, 1920, 1080, "eq=contrast=1.08:saturation=1.20"),
    ("c14_payoff_noodles", "noodles-4223987.mp4", 9.30, 1.65, 1.25, 1920, 1080, "eq=contrast=1.08:saturation=1.18"),
    ("c15_night_lamp", "night-35321895.mp4", 1.80, 5.60, 1.05, 1920, 1080, "eq=contrast=1.08:saturation=1.15"),
]


def main() -> None:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    for clip_id, fname, start_s, dur_s, speed, w, h, extra_vf in SPECS:
        src = STOCK / fname
        if not src.exists():
            raise FileNotFoundError(f"Missing stock video: {src}")
        out_dir = CLIPS_DIR / clip_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        pts_mult = 1.0 / speed
        vf = (
            f"setpts={pts_mult:.5f}*PTS,"
            f"fps=60,"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"{extra_vf}"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start_s:.3f}",
            "-t", f"{dur_s:.3f}",
            "-i", str(src),
            "-vf", vf,
            "-q:v", "3",
            str(out_dir / "f_%04d.jpg"),
        ]
        subprocess.run(cmd, check=True)
        count = len(list(out_dir.glob("f_*.jpg")))
        print(f"Extracted {clip_id:20s} -> {count:4d} frames ({w}x{h}) from {fname}")


if __name__ == "__main__":
    main()
