# 试点前边界复核与修复（2026-10-03）

本批继续对照原始评估文档及已确认的实施计划，复核已有功能的故障边界。范围是账号切换、退款核验、到期处理与恢复告警，没有新增学生或商家页面，也没有开通真实支付。保留原有未提交修改和先前验收证据。

## 已复现并修复

| 问题 | 修复与不变条件 | 复现／验收证据 |
|---|---|---|
| A 账号页面恢复焦点、身份核验未返回时，Cookie 已被另一标签换成 B，旧餐袋可能提交为 B 的订单 | 所有前端写请求共享身份核验等待；失败后重新核验。请求携带产生操作时的 `X-Yanhuo-Actor`，服务端与真实会话身份比较，错配在业务执行前返回 `409/session_changed/submitted:false`。仅增加拒绝条件，原 CSRF、对象权限及旧客户端兼容性保持；不自动重发。明确登录／注册／恢复账号和支付签名通知使用原有验证方式 | 首次浏览器失败 `frontend/test-results/identity-before-20261003/`；后端首次失败 `.runtime/session-boundary-before-20261003.log`；修复后针对回归及权限矩阵 10/10：`.runtime/session-boundary-after-20261003.log` |
| 即便等待身份核验，服务端已处理的旧 A 身份响应仍可能在 B 登录后才到达 | 服务端预期身份校验覆盖此第二个窗口。未发送或明确被拒绝的首次订单移除无意义的“结果待确认”；原有未知结果重试记录保留，A 的餐袋仍归 A | 真实 Cookie／Django 联调：`.runtime/browser-integration-20261003-175934/`；JSON 明确记录两个账号均 0 单、A 餐袋 1 份、库存 3→3 |
| 运营“外部退款核验”直接查询退款，绕过该退款已有冷却／租约；未知退款查询失败未增长退避 | 已有退款复用自身调度；未知退款号共享原付款预算，付款核验与退款查询全过程保留在途租约。失败按 10／20／30／60 秒退避，未确认成功不创建退款；陈旧租约响应不能覆盖新状态 | 先保存 3 项失败复现 `backend/test-results/external-refund-pacing-before-20261003.txt`；两个相关模块 PostgreSQL 52/52，含真实双请求和 SQL 锁边界：`backend/test-results/external-refund-financial-postgres-20261003.txt` |
| 到期工作进程有成功心跳，但被行锁持续跳过的订单仍占库存，巡检可误报正常 | 只读巡检与 worker 共享候选条件，超过到期时间 120 秒报告 `orders_expiry_backlog`。资金待核、活动付款、未结退款及被停用的模拟交易继续保留；巡检本身不修改订单或资金 | 首次失败 `.runtime/expiry-monitor-reproduced-20261003.log`；相关 SQLite 回归 13/13：`.runtime/expiry-monitor-after-20261003.log`；真实 PostgreSQL 锁场景纳入全量回归 |
| 模拟关闭后，带终态付款记录的示例订单被加入到期候选，却无法处理，可能耗尽每轮名额 | 候选条件与 `require_order_operation` 对齐，跳过这些不可处理行，让后续合法到期订单继续释放 | 同上：小批量 1 条、最老不可处理示例、后续正常订单的先失败后通过回归 |
| 实际恢复演练失败被后续普通备份成功错误宣布恢复；另一备份失败还可能覆盖原记录 | 独立持久保存 `last-restore-failure.json`，兼容旧记录；只有失败之后实际完成数据库及文件恢复比对才能解除 `restore_failed`。普通备份成功不代表可恢复 | Linux 运维测试 62/62：`.runtime/operations-followup-linux-20261003-175354.log`；独立真实 HTTPS 验证 18/18：`.runtime/monitor-followup-20261003-175332.json` |

账号标记用于防止旧页面误投操作，不能代替服务端认证。客户端可自填该标记，但无法据此获取另一个账号的身份或权限。未带标记的历史客户端保持原行为，不将其描述为具备新版 SPA 的跨标签保护。

## 本批回归

- 前端类型／构建与 15 项状态测试通过：`.runtime/identity-actor-build-20261003.log`、`.runtime/identity-actor-unit-20261003.log`。
- Chromium／WebKit 全套 **222/222**，3.9 分钟：`.runtime/identity-final-browser-20261003.log`。包含原有移动端、商家操作及结算回归。
- 身份场景 7 项 × 2 个浏览器 × 3 次重复，**42/42**，34.6 秒：`.runtime/identity-repeat-browser-20261003.log`；已加入远端 CI 的弱网重复任务，远端尚未执行。
- 真实 API 联调 **15/15**，59.5 秒：`.runtime/browser-integration-20261003-175934/browser-integration.log`。独立 SQLite／媒体及回环服务，数据和测试进程已清理。
- 模型迁移一致性通过，无新增迁移：`.runtime/boundary-migration-check-20261003.log`。
- 恢复告警演练入口定向 **16/16**：`.runtime/runtime-backup-followup-20261003-180122.log`。完整容器执行结果以本批独立 `report.json` 为准，单元测试不代替实际恢复。
- 最终 Linux 运维工具全量 **65/65**，3.686 秒：`.runtime/boundary-host-final-20261003.log`。
- 本批容器 PostgreSQL 17／PostGIS 全量 **432/432**，251.144 秒：`.runtime/runtime-20261003-180144-33aced/postgres-regression.log`，包含新增真实行锁及查询租约场景。

完整入口已由 Windows `scripts/verify-runtime.ps1 -FullRegression` 实际执行通过，**653.90 秒**；总结果及清理均为 `passed`：`.runtime/runtime-20261003-180144-33aced/report.json`。运行源码包含原有未提交修改，基线提交为 `59a6e62a05a7386196e28a1ed6800b4135e25a9f`，源码清单 SHA-256 为 `852cc072c8aa5c82bc1d5ecdb6ec3b75578782767234ceb967daaa8ff723bf0d`，镜像及主机工具摘要另列于总报告。

- HTTPS 首页／深链接／登录／API／静态与上传媒体、迁移失败阻断、三个工作进程实际终止后的恢复均通过。
- 1,000 个不同用户、受控 20 并发，50 份库存恰好成功 50 单，950 次拒绝，最终库存 0；这是服务层并发，不是 1,000 路同时 HTTP 请求。
- 正常加密回调 100/100 返回 204、2 并发，p95 **99.994 ms**。突发重放 100/100、20 并发，p95 **641.446 ms**，重复资金变更 0；两组独立统计。实际领取后结束进程并自然等待租约 **60.081 秒**，重启后资金仅改变一次。
- 全路径备份及故障演练 **11 项全部通过**，270.239 秒：`backup/backup-runtime-report.json`。初次加密捕获／上传／下载 54.09 秒，独立 PostGIS 恢复比对 22.31 秒，验证 40 张表、8 个文件。
- 实际损坏 dump 后由生产 `backup.py` 子进程执行真实 `pg_restore` 并失败，保存失败状态并发出 HTTPS 告警。`monitor-after-restore-failure.json` 显示 `restore_failed`；再次完整备份 53.56 秒后，`monitor-after-new-backup.json` 仍保留此告警；指定原良好内容的快照再次恢复 21.93 秒、比对全部表与文件通过，`monitor-after-restored-snapshot.json` 才无异常。原快照仅在测试仓库额外保留标签，生产保留策略不变。
- 所有原隔离 writer 恢复健康，API 为 200，三个新心跳和全局检查通过。总入口清理 8 个 Compose 容器、5 个卷、2 个网络及本次镜像标签；备份接收器、SFTP 和独立恢复资源也已清理。随后按三个专用标签核查无遗留容器、卷或部署网络。

这里仍是同机回环 SFTP、合成资金与虚拟支付配置；耗时只代表本次小数据演练，不作为真实异机灾备或生产 RPO／RTO 的承诺。

## 本地预览已加载修复

完整隔离验收成功后，按本项目已有进程登记、创建时间、完整命令行和父子关系核查归属，仅重新启动本项目后台及三个任务进程，没有迁移、seed 或导入测试数据。前端 5183 原进程 12340 保留，后端 8087 新监听进程为 30300；参考项目 5173／8000 原进程 38592／48380 保留。

`http://10.53.8.40:5183/` 实测 HTTP 200；通过该地址的无副作用账号错配探针返回 `409/session_changed/submitted:false`，证明服务端新拦截已实际加载。全局 `check_operations` 为 `ok`，三个心跳正常、无可释放到期积压。证据：`.runtime/preview-boundary-reload-20261003-181353.json`、`.runtime/boundary-preview-health-20261003.log`。无需更换手机访问地址。

## 试点开通边界

原评估文档的 20 项建议及用户确认的例外继续按[整改清单](audit-remediation-checklist.md)执行；本批修复了其中账号隔离、统一支付查询预算、过期处理监测和恢复可靠性的遗漏。此前规模与运行证据保留在[运行证据索引](runtime-evidence-20261003.md)。

远端 Actions 已配置但本批未推送执行。真实服务器、域名、逐商户支付与账单、异机存储、值守告警、Android／iPhone／微信实机及校园现场 48 小时观察仍待各自验收。不能以本机自动化通过证明 5–15 家真实商户已能独立经营，PWA、骑手端及其他延期事项也不在本批补做。
