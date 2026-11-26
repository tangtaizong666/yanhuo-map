"""Synthesize studio-grade Chinese voiceover cues via edge-tts and build exact timeline.json."""
import asyncio
import json
import subprocess
from pathlib import Path
import edge_tts

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "audio"
VO_DIR = AUDIO_DIR / "vo_clips"

VOICE = "zh-CN-YunxiNeural"
RATE = "+2%"
PITCH = "-2Hz"

# Each cue: (id, act_label, text, subtitle_chunks, gap_before_sec, gap_after_sec)
# subtitle_chunks splits each cue into concise readable cinema subtitle lines with proportional weights
CUES = [
    (
        "c1_hook",
        "ACT I · 22:10 校门口",
        "晚上十点下课，校门口这口八块钱的热乎烤冷面，为什么总让人两难？",
        [
            "晚上十点下课，校门口这口 8 块钱的热乎烤冷面",
            "为什么总让人两难？",
        ],
        1.10,
        0.55,
    ),
    (
        "c2_pain",
        "ACT I · 跑空与溢价",
        "走过去怕扑空，点外卖翻一倍，小摊贩也被挡在合规与高抽佣之外。",
        [
            "走过去怕扑空，点外卖翻一倍",
            "小摊贩也被挡在合规与高抽佣之外",
        ],
        0.20,
        0.95,
    ),
    (
        "c3_freshness",
        "ACT II · 60分钟鲜度闸门",
        "于是，我们做了烟火地图。摊主一键确认出摊，全校实时可见；超过六十分钟未续期，系统自动落下停单闸门，彻底告别跑空。",
        [
            "于是，我们做了烟火地图",
            "摊主一键确认出摊，全校实时可见",
            "超 60 分钟未续期自动落闸停单，彻底告别跑空",
        ],
        0.35,
        0.95,
    ),
    (
        "c4_compliance",
        "ACT III · A/B分层与5%光学分账",
        "面对监管红线，系统硬核分层：流动摊零门槛免费找摊，持证店才开放线上预付。微信特约直连分账，九成五直达摊主，平台只收百分之五技术服务费，不设二清资金池。",
        [
            "面对监管红线：流动摊免费找摊，持证店才开放交易",
            "微信特约直连分账：95% 直达摊主，仅 5% 技术服务费",
            "平台零二清资金池，不碰商户一分钱",
        ],
        0.35,
        0.95,
    ),
    (
        "c5_live_app",
        "ACT IV · 三端全栈实机联动",
        "这不是纸面构想，而是已经跑通的三端全栈实机。学生端逐份备注口味，预付锁单，凭四位码两秒即取；商家端专为油手忙时打造，一键出餐，还能拍张照辅助上架。",
        [
            "不是纸面构想，而是已跑通的三端全栈实机",
            "学生端逐份备注、预付锁单、凭 4 位码两秒即取",
            "商家端油手大键一键出餐，拍张照辅助轻松上架",
        ],
        0.35,
        0.90,
    ),
    (
        "c6_governance",
        "ACT V · 保温交接点与平台治理",
        "搭配校门固定保温交接点，与独立脱敏的平台治理台，让校方监管放心，让校园烟火有序生长。",
        [
            "搭配校门固定保温交接点与独立脱敏治理台",
            "让校方监管放心，让校园烟火有序生长",
        ],
        0.35,
        1.10,
    ),
    (
        "c7_finale",
        "ACT VI · 烟火志终章",
        "一盏摊灯，一份热食，一座有温度的数字校园。烟火地图，少跑一次空，留住校门口的热乎气。",
        [
            "一盏摊灯，一份热食，一座有温度的数字校园",
            "烟火地图 · 少跑一次空，留住校门口的热乎气",
        ],
        1.30,
        3.20,
    ),
]


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


async def synth_all() -> None:
    VO_DIR.mkdir(parents=True, exist_ok=True)
    cursor = 0.0
    timeline_cues = []

    for cid, label, text, subs, gap_before, gap_after in CUES:
        mp3_path = VO_DIR / f"{cid}.mp3"
        wav_path = VO_DIR / f"{cid}.wav"
        comm = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE, pitch=PITCH)
        await comm.save(str(mp3_path))

        # Trim tiny leading/trailing digital silence & convert to 48kHz mono WAV with crisp vocal EQ
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(mp3_path),
                "-af",
                "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-48dB,"
                "areverse,silenceremove=start_periods=1:start_silence=0.05:start_threshold=-48dB,areverse,"
                "highpass=f=85,equalizer=f=3200:t=q:w=1.2:g=2.2,alimiter=limit=0.92",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(wav_path),
            ],
            check=True,
        )
        dur = probe_duration(wav_path)
        start = round(cursor + gap_before, 3)
        end = round(start + dur, 3)
        cursor = round(end + gap_after, 3)

        # Split subtitle lines proportionally by character count
        weights = [max(len(s), 1) for s in subs]
        total_w = sum(weights)
        sub_cursor = start
        sub_entries = []
        for idx, (line, w) in enumerate(zip(subs, weights)):
            s_start = round(sub_cursor, 3)
            s_end = round(end if idx == len(subs) - 1 else sub_cursor + dur * (w / total_w), 3)
            sub_entries.append({"start": s_start, "end": s_end, "text": line})
            sub_cursor = s_end

        timeline_cues.append(
            {
                "id": cid,
                "label": label,
                "start": start,
                "end": end,
                "duration": round(dur, 3),
                "wav": f"audio/vo_clips/{cid}.wav",
                "text": text,
                "subtitles": sub_entries,
            }
        )
        print(f"{cid:16s}  {start:6.2f}s -> {end:6.2f}s  (dur {dur:5.2f}s)  {label}")

    total_duration = round(cursor, 2)
    # Snap total duration to clean 0.5s grid for exact 60fps frame count
    total_duration = round(total_duration * 2) / 2.0
    total_frames = int(round(total_duration * 60))

    # Build exact scene boundaries anchored directly to VO cues!
    c = {item["id"]: item for item in timeline_cues}
    scenes = [
        {
            "id": "s01_hero_griddle",
            "title": "22:10 校门口 · 烤冷面烟火开场",
            "start": 0.0,
            "end": round(c["c1_hook"]["end"] + 0.30, 3),
        },
        {
            "id": "s02_empty_gate",
            "title": "人呢？又跑空了？",
            "start": round(c["c1_hook"]["end"] + 0.30, 3),
            "end": round(c["c2_pain"]["start"] + c["c2_pain"]["duration"] * 0.36, 3),
        },
        {
            "id": "s03_receipt_dissection",
            "title": "价格断层 · ¥8.00 vs ¥16.50 热敏小票对决",
            "start": round(c["c2_pain"]["start"] + c["c2_pain"]["duration"] * 0.36, 3),
            "end": round(c["c2_pain"]["end"] + 0.70, 3),
        },
        {
            "id": "s04_brand_map",
            "title": "烟火地图揭幕 · 校门动态地图",
            "start": round(c["c2_pain"]["end"] + 0.70, 3),
            "end": round(c["c3_freshness"]["start"] + c["c3_freshness"]["duration"] * 0.52, 3),
        },
        {
            "id": "s05_freshness_flap",
            "title": "60分钟鲜度翻页闸门 · 超时自动停单",
            "start": round(c["c3_freshness"]["start"] + c["c3_freshness"]["duration"] * 0.52, 3),
            "end": round(c["c3_freshness"]["end"] + 0.70, 3),
        },
        {
            "id": "s06_dual_track",
            "title": "G2026 A/B 双轨合规高速路牌",
            "start": round(c["c3_freshness"]["end"] + 0.70, 3),
            "end": round(c["c4_compliance"]["start"] + c["c4_compliance"]["duration"] * 0.44, 3),
        },
        {
            "id": "s07_optical_prism",
            "title": "微信特约光学分账 · 95% 直达 / 5% 零二清",
            "start": round(c["c4_compliance"]["start"] + c["c4_compliance"]["duration"] * 0.44, 3),
            "end": round(c["c4_compliance"]["end"] + 0.70, 3),
        },
        {
            "id": "s08_student_live",
            "title": "学生端实机 · 逐份备注与 4 位取餐码",
            "start": round(c["c4_compliance"]["end"] + 0.70, 3),
            "end": round(c["c5_live_app"]["start"] + c["c5_live_app"]["duration"] * 0.56, 3),
        },
        {
            "id": "s09_merchant_ai",
            "title": "商家端实机 · 油手大键与拍图辅助上架",
            "start": round(c["c5_live_app"]["start"] + c["c5_live_app"]["duration"] * 0.56, 3),
            "end": round(c["c5_live_app"]["end"] + 0.65, 3),
        },
        {
            "id": "s10_handoff_ops",
            "title": "校门保温交接点 × /ops 独立脱敏治理台",
            "start": round(c["c5_live_app"]["end"] + 0.65, 3),
            "end": round(c["c6_governance"]["end"] + 0.85, 3),
        },
        {
            "id": "s11_montage_finale",
            "title": "烟火志终章 · 一盏摊灯一份热食",
            "start": round(c["c6_governance"]["end"] + 0.85, 3),
            "end": total_duration,
        },
    ]

    manifest = {
        "title": "烟火地图 ·《烟火志》三创赛官方品牌宣传片",
        "voice": VOICE,
        "fps": 60,
        "width": 1920,
        "height": 1080,
        "duration_seconds": total_duration,
        "total_frames": total_frames,
        "cues": timeline_cues,
        "scenes": scenes,
    }
    out_json = ROOT / "assets" / "timeline.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nMaster timeline written -> {out_json}")
    print(f"Total duration: {total_duration:.2f}s ({total_frames} frames @ 60fps)")
    for s in scenes:
        print(f"  {s['id']:22s}  {s['start']:6.2f}s -> {s['end']:6.2f}s  ({s['end']-s['start']:5.2f}s)  {s['title']}")


if __name__ == "__main__":
    asyncio.run(synth_all())
