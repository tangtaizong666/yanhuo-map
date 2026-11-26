# 烟火地图 ·《烟火志》品牌与路演宣传片（81.5s · 1080p60）

本目录包含 **烟火地图（Yanhuo Map）** 全新重构的 **81.5 秒 · 1920×1080 · 真 60fps（4,890 帧）** 官方品牌与三创赛答辩宣传片，视觉语言与终版路演《烟火志》完全统一，并升级加入**中文专业旁白、帧级同步字幕、暖调电钢琴配乐与真实三端实机画面**。

## 核心亮点

1. **三端全栈真机实拍画面**：通过独立隔离实例（`tools/capture_app_screens.py`）实时跑通下单、模拟预付、自动接单、4 位取餐码核销与 `/ops` 脱敏监督全链路，抓取 2× Retina 实机画面（学生端首页、4 位取餐码 `3902`、商家端油手大键接单台与待取餐、平台治理台 `/ops` 营收分账与脱敏订单）。
2. **完整呈现最新商业与合规架构**：
   - 深夜跑空痛点与 `¥8.00 vs ¥16.50` 热敏小票对比盖章；
   - `60:00 → 停单` 机械翻页闸门（超时自动停止新单防跑空）；
   - `G2026` A/B 双轨合规分流高速路牌（纯流动摊 0 元免费找摊，持证店核验准入）；
   - 微信特约直连光学棱镜分账（`100% → 95.0% 商户净额 + 5.0% 技术服务费`，平台零二清资金池）；
   - 商家油手大键一键出餐、拍图辅助上架、校门固定保温交接点与 `/ops` 脱敏治理台；
   - 「一盏摊灯，一份热食，一座有温度的数字校园」通行票根情感收束。
3. **广播级声音设计与零版权风险**：`edge-tts` 中文旁白 + 帧级同步字幕 + 侧链自动避让（Sidechain Ducking）的原创暖调电钢琴律动配乐与拟音（铁板滋啦、盖章、翻页机械声、光学棱镜泛音），经 FFmpeg EBU R128 响度母带处理至约 `-14 LUFS`。

## 复现与核验命令

```powershell
# 1. 在独立临时端口抓取三端 2x Retina 实机截图（不触碰 5183/8087 与开发库）
python promo/yanhuo-cinema/tools/capture_app_screens.py

# 2. 合成中文旁白与精确时间轴 timeline.json
python promo/yanhuo-cinema/tools/build_voiceover.py

# 3. 合成配乐、拟音与侧链避让母带 soundtrack.wav
python promo/yanhuo-cinema/tools/create_audio.py

# 4. 仅渲染 16 张关键帧与 contact-sheet.jpg 快速审片
node promo/yanhuo-cinema/tools/render.mjs --keyframes

# 5. 全量 60fps (4,890 帧) 并行渲染并封装 BT.709 H.264 + AAC 成片
node promo/yanhuo-cinema/tools/render.mjs

# 6. 校验编码、帧数、色彩参数与音频响度
python promo/yanhuo-cinema/tools/verify.py
```
