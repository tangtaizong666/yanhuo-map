# 审计整改清单与试点开通边界

依据仓库内《Yanhuo Map — Comprehensive Code Audit, Product Assessment, and Roadmap》，审计版本 `59a6e62`。具体实施范围以用户确认的七阶段计划为准；文档中的产品建议不自动替代已确认的业务规则。

## 原文 20 项开发任务

| 原文任务 | 代码与入口 | 验收条件与状态 |
|---|---|---|
| 限制客户／摊位占位 | `backend/market/reservation_limits.py`、`services.py`；`CheckoutView.vue` | 已实现 1／3／10。账号锁内检查，原键重试先于新额度。正常终态释放；资金未核清继续占位，不能只看是否返还库存。并发与历史大单重放纳入 PostgreSQL 回归。 |
| 下单专用限频 | `reservation_limits.py`、`config/settings.py` | 已实现每账号 6 笔／分钟、30 笔／小时。只计已创建订单，取消仍计数，幂等重放不再创建；错误返回稳定码及等待时间。 |
| 支付查询节流 | `payment_schedule.py`、`payments.py`、`reconciliation.py` | 已实现付款／退款独立调度，默认 5 秒，失败退避 10／20／30／60 秒；HTTP、后台和运营查询共享预算。可信通知、关单及退款创建仍按各自状态机运行。 |
| 持久微信通知收件箱 | `notification_models.py`、`notification_inbox.py`、迁移 `0016` | 已实现验签解密、必要资金字段落库、提交后 204，账户＋事件去重及内容冲突记录；worker 租约／失败重试／崩溃恢复。测试不替代真实商户回调验收。 |
| 回调入口保护 | `config/callback_wsgi.py`、`payment_views.py`、`deploy/Caddyfile`、`compose.yaml` | 已实现独立受限进程、2 MiB、两处理槽和过载失败响应。真实 Caddy 请求大小与可信代理测试已执行；生产并发与真实验签延迟待目标环境验收。 |
| 公开摘要与详情分离 | `serializers.py`、`discovery_queries.py`、前端 `lib/types.ts` | 已实现公开／商家类型拆分；摘要每卡最多两餐点，详情单独读。公开不含精确库存、库存版本、备餐量、配送容量或原始接单心跳；电话默认不公开。 |
| 分页及地图范围查询 | `discovery_api.py`、`HomeView.vue`、`MapView.vue` | 已实现默认 20／最多 50、服务端搜索排序、独立餐点搜索；地图视口最多 200 点，超限提示缩小范围。100／500／2000 摊位实测见规模报告。 |
| 持续集成 | `.github/workflows/verify.yml`、`playwright.ci.config.ts`、`scripts/verify_browser_integration.py` | 已配置 PostgreSQL/PostGIS、迁移、类型／构建、状态测试、Chromium/WebKit、依赖审计和失败产物。远端实际四项检查已通过，首轮失败和修复见[远端 CI 记录](remote-ci-20261003.md)；保护分支必过检查没有自动更改。 |
| 独立生产迁移 | `compose.yaml`、[发布指南](deployment-release.md) | 已实现单次 release 门控；本批 WSL/Linux 与远端 CI 的实际失败迁移阻断、完整 HTTPS 启动及3个 worker恢复均已通过。本地开发方式保留；真实目标服务器仍需实际验收。 |
| 浏览器安全策略 | `deploy/Caddyfile` | SPA 已强制禁止嵌入；完整资源 CSP 为报告阶段。正式高德、二维码、支付跳转通过兼容验收后再收紧执行。 |
| 移除任意商家外链图片 | `media_policy.py`、`merchant.py`、前端 `lib/media.ts` | 新图片仅平台上传／受控路径；旧值保留但公开与商家浏览器都不请求外链，商家可重新上传。订单图片快照也过滤显示；不覆盖已有上传文件。 |
| 账号失败冷却 | `auth_limits.py`、`recovery.py`、`views.py` | 已实现跨 IP 的 5／10／20／30 秒短冷却，只在放行验证失败时推进。成功或有效恢复清账户桶，来源 IP 限制保留；不宣称消除所有针对性登录干扰。 |
| 商家激活清单 | `serializers.py`、`MerchantView.vue`、`MerchantStore.vue` | 已实现申请建档、菜单、资质、真实位置、公开、交易、可选支付配送及负责方／原因。隐藏摊位无公开分享与无效预览入口，保留全站学生预览。 |
| 同一面板完成取餐交接 | `MerchantOrders.vue` | 金额、收款状态、取餐码统一呈现；确认收款与核销保持两个独立操作，未收款不能核销。弱网重放及两个步骤分离有浏览器回归。 |
| 商家动作幂等 | `security_models.py`、`services.py`、迁移 `0018`、`MerchantOrders.vue` | 新客户端 12 种经营动作保留原请求键；先权限后重放，同键异内容拒绝，历史备餐键继续识别。旧调用可不带键，但无键不承诺自动重放；退款另沿用专用资金幂等。 |
| 前端状态测试层 | `frontend/unit/`、`vitest.config.ts`、发现／结算／支付／商家 E2E | 已加入真实 store 单元测试；账号切换、餐袋与草稿隔离、晚到响应、查询倒计时、断网动作和原键恢复纳入回归。具体执行数见验证台账。 |
| 生产备份与恢复 | `scripts/backup.py`、`deploy/systemd/`、[恢复指南](backup-recovery.md) | 代码、加密虚拟数据备份演练、独立 PostgreSQL 恢复验证已完成。真实异机目的地、密钥／媒体恢复、告警与 RPO 1 小时／RTO 2 小时仍待实际演练。 |
| 实体设备验收包 | [发布与 48 小时观察](deployment-release.md) | 已提供 Android／iPhone／微信浏览器核验项。没有真实设备操作证据，不以 Playwright WebKit 代替 iPhone 验收。 |
| 可信业务指标与浏览事件分开 | `pilot_metrics.py`、`admin.py`、迁移 `0019`、`MerchantAnalytics.vue` | 已实现真实／模拟分开、商户开摊天数、接单 p50/p95、完成／收款人数和核实位置反馈；复购沿用既有服务端履约记录。运营确认须填写核实依据，处理完成不等于属实。 |
| PWA 商家提醒实验 | 既有前台声音／提示保留 | 按用户计划延期到试点观察后；不新增页面关闭或锁屏后可靠接单承诺。 |

另已将默认 API 权限改为登录必需，所有预期公开路由显式 `AllowAny`，用完整路由矩阵测试防止新增接口默认为公开。新增 `+5／+10` 加入补货草稿，沿用原批次确认与幂等，暂停供应不受补货影响。

## 验证与实际开通

结果及原始日志索引见 [本轮验证台账](audit-operations-verification-20261003.md)，规模测量及条件见 [发现接口实测](discovery-scale-20261003.md)。原有历史验证记录不覆盖。

后续运行验收已完成本机和远端 CI 部分；统一入口、PostgreSQL 回归、真实加密通知、实际 SFTP/restic/PostGIS 恢复及告警的历次证据见[运行验收索引](runtime-evidence-20261003.md)，最新全量为432项通过。主机巡检每60秒运行并持久保存投递状态；同机恢复仍不能代替异机灾备。

继续逐项复核时发现并修复了账号切换提交竞态、运营退款核验绕过查询预算、健康心跳掩盖到期积压及恢复失败被普通备份误清除。先失败后修复的证据与更新后的回归结果单列于[试点边界复核](pilot-boundaries-20261003.md)，原有证据继续保留。

校园交接点配送和 Native/H5 支付均已纳入代码准备与隔离测试。服务器、HTTPS 域名、真实商户支付权限、异机备份与值守条件齐备后，逐商户验收付款、通知、查单、关单、退款、账单、配送交接及异常收餐；只开放已通过的商户／服务，观察 48 小时后再扩到 5–15 家。

本轮不新增骑手端、跨摊合单或取消精确库存核验的模式。不编造迁址审核时限；PWA、结构化评价和更丰富商家故事留待真实试点问题确定投入。页面访问事件、模拟订单及本地测试数量均不能当作真实交易或用户采用证据。
