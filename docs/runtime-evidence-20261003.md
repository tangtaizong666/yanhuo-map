# 单校园试点前运行验收证据索引（2026-10-03）

后续账号切换、统一退款查询、到期积压与恢复告警的修复及新增回归见[试点边界复核](pilot-boundaries-20261003.md)。本页保留上一批原始结果，不覆盖其首次失败或通过记录。

用户后续批准的真实 GitHub Actions 执行、首轮失败及对应修复单列于[远端 CI 记录](remote-ci-20261003.md)。本页下方“远端未运行”是此前批次的历史状态，最新状态以该记录和 PR 的实际检查为准。

本批范围是用户批准的隔离部署、真实并发、任务恢复、巡检告警及备份恢复。业务 API、订单限额、数据库结构和前端页面未在本批扩展。Git 基线仍为 `59a6e62a05a7386196e28a1ed6800b4135e25a9f`；原有未提交修改保留，实际构建使用工作区源码，不能把基线提交号独自当成运行版本。每次部署报告另含源码清单 SHA-256、镜像 ID、项目名和证据路径。

**本机可独立完成的本批验收已通过。** 最终由 Windows 的 `scripts/verify-runtime.ps1` 调用同一 Linux 入口，完整运行 **404.18 秒**，总结果及清理均为 `passed`：`.runtime/runtime-20261003-171931-aa4b0f/report.json`。构建源码清单摘要为 `01bb09036f8ae9386a4bd74a459e58af2433b17d13d296dbdc3a53692a79758e`，主机脚本另在总报告记录实际调用时的摘要。

## 本批已实测

| 项目 | 实际结果 | 原始证据 |
|---|---|---|
| 完整 Linux 部署 | 失败迁移阻断 API／回调／3 workers；解除后成功发布；HTTPS 首页、深链接、登录、API、静态文件、图片上传读回通过 | `.runtime/runtime-20261003-170204-d9c6e6/report.json` |
| 工作进程恢复 | 3 个原容器自动重启，各自有终止之后的新成功心跳；排队订单随后到期，库存仅返还 1 份；再次执行不重复释放 | 同上 `worker_recovery` |
| 全量 PostgreSQL 回归 | 容器内 **411/411**，**242.432 秒**；PostgreSQL 17.5／PostGIS 3.5 | `.runtime/runtime-20261003-170535-6e5c4e/postgres-regression.log` |
| 库存争抢 | 1,000 个不同用户、20 并发服务层创建；50 份成功、950 次拒绝、最终库存 0、50 次库存变更 | 同目录 `report.json` 的 `RUNTIME_STOCK_RESULTS` |
| 同键与全站额度 | 同键 20 并发只创建 1 单、重放 19 次；同账号 4 摊并发只成功 3 单；取消、到期释放和待核占位通过 | 同上 `RUNTIME_ORDER_REPLAY_RESULTS`／`RUNTIME_GLOBAL_QUOTA_RESULTS` |
| 正常加密回调 | 真实 Waitress、临时 RSA-2048 SHA256 + AES-256-GCM；100 次、2 并发全部 204，p95 **87.960 ms** | 同上 `RUNTIME_NOTIFICATION_NORMAL_RESULTS` |
| 突发重放 | 100 次、20 并发全部 204，重复资金变更 0；p95 **677.218 ms**，单独统计，不混入正常负载指标 | 同上 `RUNTIME_NOTIFICATION_BURST_RESULTS` |
| 通知故障恢复 | 订单行锁下收件、入库后实际结束回调进程、领取后强制结束进程、自然等待 60.026 秒租约、退款乱序及冲突通过；过载解除后有效通知继续入账一次 | 同目录 `report.json` 与 `notification-processes/` |
| 主机巡检 | 实际运行容器和 workers 时注入死信、冲突、资金待核可被发现；解除后恢复；缺备份证据明确报 `backup_unavailable` | 部署报告 `monitor` |
| HTTPS 告警 | 独立接收器：503、跨进程重启重试、204、恢复一次、拒绝不可信证书／重定向。重试时钟为测试逻辑时钟 | 部署证据 `monitor-https.json` |
| 运维安全回归 | **56/56**，包括归属校验、拒绝误删、失败日志、备份失败、投递状态持久化、来源不可读时不误报恢复，以及最终数据库就绪与服务恢复验证 | `.runtime/runtime-final-operations-complete-20261003.log` |
| 前端类型／构建与状态测试 | 构建通过，状态测试 **15/15** | `.runtime/runtime-final-build-20261003.log`、`runtime-final-unit-20261003.log` |
| 浏览器回归 | Chromium／WebKit **208/208**，3.8 分钟 | `.runtime/runtime-final-browser-20261003.log`、`frontend/test-results/runtime-final-20261003/` |
| 弱网重复运行 | 关键结算、商家动作与支付轮询独立重复 3 次，**111/111**，2.0 分钟；独立保存产物，不覆盖首次回归 | `.runtime/runtime-final-weak-network-20261003.log`、`frontend/test-results/runtime-weak-network-20261003/` |
| 实际 API 浏览器联调 | 临时 SQLite／媒体、合成账户，**14/14**；测试服务与临时数据已清理 | `.runtime/browser-integration-20261003-170214/` |

上述 PostgreSQL 全量运行的备份后续阶段曾失败，因此其**总报告仍为 failed**；只能引用已经通过的子阶段。修正备份工具后，最新完整入口再次通过部署、恢复、巡检、8 项 PostgreSQL 并发／通知验收及全路径备份；未改动的后端业务代码沿用已通过的 411 项全量结果，不把两次执行混称成一次。

最新通知正常 cohort 100/100 返回 204，2 并发 **p95 99.540 ms**；突发重放 100/100、20 并发 **p95 658.353 ms**，资金重复变更 0，仍单列。最新库存争抢为 1,000 用户／20 并发，50 成功／950 拒绝、库存 0。8 项定向测试共 99.625 秒，原始日志为最终目录的 `runtime-acceptance.log`。

库存并发不冒充 HTTP 负载或 1,000 路同时连接。通知 p95 只在正常 cohort 全部成功时成立；无效签名、大请求体、半开连接过载、主动断开回调等故障分别记录。复核后的 Windows PostgreSQL 17.11 定向测试亦为 8/8，正常 p95 121.714 ms，见 `backend/test-results/runtime-acceptance-reviewed-20261003-postgres.txt`，不与 Linux 指标混合。

## 备份与故障记录

首轮完整备份已经实际暂停写入，执行 `pg_dump`、收集媒体和虚拟支付配置，经 SFTP/restic 加密上传、下载校验成功，耗时 53.64 秒。随后独立 PostGIS 恢复失败；失败证据保留在 `.runtime/runtime-20261003-170535-6e5c4e/backup/`，不能据此宣称恢复验收通过。

修复后的完整备份报告是 `.runtime/runtime-20261003-171931-aa4b0f/backup/backup-runtime-report.json`，结果 **passed**：

- 实际暂停写入、dump、加密上传及下载校验 **53.39 秒**；独立断网 PostGIS 恢复与比对 **23.24 秒**，验证 **40 张表、8 个文件**。完整备份故障演练共 **188.936 秒**。
- 6 类资金相关表均非空并参与全表指纹核对：2 笔订单、2 条明细、1 次付款、2 次退款、1 条资金证据、2 条通知。保留退款替换关系、查询调度和逐份要求；模拟记录不当成真实交易。
- 实际注入 dump 失败后，原 5 个 writer 容器恢复；HTTPS 告警 503 后保留持久失败及未投递状态；媒体损坏被文件清单拒绝；损坏 dump 导致真实 `pg_restore` 失败后，专用恢复容器和卷仍清理成功。
- 全部故障完成后，再验证原 5 个容器健康、API HTTP 200、3 个 worker 的心跳晚于最后启动时间、`check_operations` 为 `ok`。合成 SFTP 容器／卷及 HTTPS 接收器清理完成，总入口也清理了 8 个 Compose 容器、5 个卷和2个网络。

以上使用同机回环 SFTP、合成资金和虚拟支付配置，证明恢复代码路径和本次数据完整性；真实异机灾备、真实密钥及生产 RPO／RTO 仍待验收。

构建路径、回调拒绝状态断言和 Docker 人工停止语义在验收程序调试中修正，首次失败均保留在 `.runtime/runtime-20261003-164632-fa2dd6/`、`164649-2d57e0/`、`165235-498ae5/`、`165509-93f55c/`、`165704-ed6c38/`。它们不属于支付或库存生产缺陷，也不作为通过证据。

本批发现的实际运维缺陷包括：备份程序固定数据库名、失败后缺持久状态、巡检把不可读来源误当恢复，以及恢复探针误认初始化临时 Unix 数据库服务。后者导致导入期间被初始化关库中断，已改为等待最终 TCP 监听；首次精确复现和修复后独立恢复比对分别见 `.runtime/restore-probe-6b3ab8daba107117/` 和 `.runtime/restore-probe-fab745f509653484/report.json`。对真正运行过的失败如实保留，不用后来的通过覆盖。

## 历史规模证据

此前 PostgreSQL **100／500／2,000** 摊位、最多 **23,989** 商品的结果已补录到[规模报告](discovery-scale-20261003.md)，原始数据位于 `backend/test-results/audit-financial-final-20261003-postgres.txt` 的 `DISCOVERY_SCALE_RESULTS`。它与 SQLite 历史结果分列。

本批 Linux 全量回归再次执行同一规模测试，机器数据在 `.runtime/runtime-20261003-170535-6e5c4e/report.json`：2,000 摊位时列表 20 条约 23.40 ms、50 条约 38.59 ms、商品 20 条约 34.65 ms、地图 200 点约 32.12 ms；SQL 为 3／3／3／2 次。均为顺序请求三次中位数；Python 分配峰值并非整机内存、生产并发能力或真实用户体验。

## CI 和外部待验收

远端 `.github/workflows/verify.yml` 已接入共用完整入口及弱网重复运行，并保存日志、截图和 trace。**本批未推送或实际运行远端 Actions，远端 CI 仍待执行**；YAML 已解析，不能以解析通过代替执行。

最终检查，本机服务 5183／8087 和参考项目 5173／8000 的原进程持续保留，PID 分别为 12340／25252／38592／48380；`http://10.53.8.40:5183/` 仍返回 200。Docker 原有 `astrbot`／`napcat` 不在本次标签范围，未停止或改动。按本批部署、备份和恢复标签查询，没有遗留容器或数据卷。

真实服务器、HTTPS 域名、逐商户 Native/H5 支付与账单、异机备份目的地、告警接收人、Android／iPhone／微信实机及校园 48 小时观察均分别保持待验收。本机 SFTP 演练不代表异机灾备，局部小数据恢复耗时不代表已承诺生产 RPO 1 小时／RTO 2 小时。

执行入口见[运行验收指南](runtime-acceptance.md)，既有审计和产品边界见[审计整改清单](audit-remediation-checklist.md)。
