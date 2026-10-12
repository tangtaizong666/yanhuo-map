# 全局巡检与可靠告警

主机每 60 秒运行 `scripts/operations_monitor.py check`。工具只读检查容器、数据库中的工作进程与资金状态，以及备份证据；不重启服务，不更改订单、库存、付款或退款。

## 部署

1. 将 `deploy/operations.example.json` 复制到仓库外 `/etc/yanhuo/operations.json`，设置实际 Compose 项目名、目录、生产环境文件及备份状态目录。启用了支付挂载时，`compose_files` 使用 `["compose.yaml", "compose.payments.yaml"]`，与发布命令一致。
2. 将告警接收端 HTTPS URL 单独写入 `/etc/yanhuo/operations-webhook`。配置文件、生产环境文件及 webhook 文件须为绝对路径且权限 `0600`。不在仓库、命令行参数或日志中放 URL/凭据。接收端需要接受本文的通用 JSON 格式；现成聊天机器人如有不同协议，需要服务端适配器。
3. 创建 `/var/lib/yanhuo-monitor`，权限 `0700`。备份新鲜度读取生产备份工具的 `/var/lib/yanhuo-backup/last-success.json`，以 `captured_at` 计算 RPO，不以较晚的上传时间计算。
4. 先运行一次下方命令，确认报告，再以 `0644` 权限安装 `deploy/systemd/yanhuo-operations-monitor.service` 和 `.timer` 到 `/etc/systemd/system/`，执行 `systemctl daemon-reload`、`systemctl enable --now yanhuo-operations-monitor.timer`。部署目录不同要同步修改 service 路径；状态目录改变时也要修改 `ReadWritePaths`。不要将示例文件直接作为生产配置使用。

```sh
python3 /srv/yanhuo/scripts/operations_monitor.py check --config /etc/yanhuo/operations.json
systemctl list-timers yanhuo-operations-monitor.timer
journalctl -u yanhuo-operations-monitor.service
```

默认阈值：成功心跳 120 秒；通知与真实资金积压 600 秒；可安全释放订单超过到期时间 120 秒；备份捕获距今 3,600 秒。订单阈值由 `expiry_backlog_seconds` 配置，查询与到期 worker 使用同一候选条件，排除资金待核、在途付款/退款及当前模式不允许处理的模拟单。死信、冲突和资金待人工核查首次发现即告警。所有常驻服务及 release 结果都检查；容器存活不能掩盖业务处理异常。`check_operations` 不可执行、响应格式错误、漏报工作进程或缺少到期订单诊断都会成为异常。

检查来源暂时不可读时，只报告来源异常；保留其先前异常记录，不凭缺少数据宣布恢复。来源恢复可读后再确认业务恢复。无法确认容器项目和目录标签时，不执行其 backend 命令。

## 投递与状态

同类故障初次出现发送 `problem`，持续故障成功通知后每 1,800 秒发送 `reminder`，恢复发送一次 `recovery`。通知先写入 `monitor-state.json` 再进行 HTTPS 请求。失败保留原事件 ID，按 60/120/240/480/900 秒退避；每次巡检在首次投递失败后停止发送，避免故障端点同时收到多类重试。重启后继续使用原队列和去重状态。

每次通知只包含 `event_id`、`kind`、`category`、`count`、`observed_at`、`first_seen_at`、`runbook`。不包含客户信息、订单内容、数据库连接、网关返回正文或支付凭据。`Idempotency-Key` 请求头等于稳定的 `event_id`。接收端按该 ID 去重：网络超时或接收成功后主机崩溃仍可能重试同一事件，不能承诺跨 HTTP 系统的恰好一次投递。

仅接受 HTTPS 成功的 2xx 回应，禁止重定向，禁用环境代理，不关闭证书校验。正常运行使用系统 CA；隔离验收可在私有配置里提供 `ca_file`，只在此客户端信任测试证书，不修改系统信任。

`last-check.json` 是最近结果；`monitor-state.json` 保存当前异常、待投递队列和最近 256 次成功投递。两个文件都以原子替换持久化。状态文件损坏时保持原文件并返回失败，禁止清空后重发全部通知。维护时保留整个状态目录。

退出码：`0` 全部正常且队列清空；`1` 有业务异常或投递待完成；`2` 配置、持久化、锁或状态错误。systemd 将非零结果留在 journal，下一次 timer 仍继续检查。告警通道故障也会留下本地失败记录；主机完全离线无法由主机自身上报，需要外部监控，此项仍待生产接收方配置。

备份工具自身仍会即时投递失败告警；全局巡检另外读取其持久失败记录。这是两个通知来源，生产接收端应做事件关联或配置不同路由，避免重复打扰同一联系人；全局巡检的去重不声称覆盖备份工具的独立 webhook。

恢复演练失败另存 `last-restore-failure.json` 并报告 `restore_failed`。后续普通备份成功、状态检查或服务恢复都不能解除它；必须有晚于失败、完成表和文件比对的 `last-restore.json` 才通知恢复。旧版本 `last-failure.json` 中的 `restore-check` 失败也按此处理，新的备份失败不会覆盖独立恢复失败记录。

## 处理入口

### containers-unavailable
核查 Docker daemon、Compose 文件和项目/目录标签。巡检拒绝检查标签不符的容器；不要以关闭全部容器或删除卷的方式排障。

### containers-unhealthy
查看本项目 `docker compose ps --all` 和异常服务日志。确认数据库、API、回调、三个工作进程、Caddy 均正常。

### release-failed
先修复迁移或静态文件发布失败。release 未成功前保持应用阻断，不绕过依赖启动。

### operations-unavailable
运行同配置的 `python manage.py check_operations`，查看 API 容器与数据库连通性。不要将命令失败解释成无积压。

### worker-expire-orders
### worker-reconcile-payments
### worker-process-payment-notifications
检查对应工作进程日志、最后成功心跳、数据库连接和工作项处理错误。核对处理确实推进后再判断恢复。

### orders-expiry-backlog
可安全释放的订单已到期超过阈值，仍未处理。核查 `expire_orders` 实际处理量、数据库长事务/行锁和批次吞吐；新心跳不等于库存已释放。按现有到期流程处理，不直接改库存或绕过付款/退款核验。

### notifications-backlog
### notifications-dead
### notifications-conflicts
使用管理员通知收件箱定位积压、死信或冲突。先核验资金状态和内容，再按既有人工重处理流程操作。冲突证据不自动清除；本工具不会修改通知记录。

### payments-backlog
### payments-review
按资金对账流程核验真实支付/退款和待人工核查订单。不要因本地超时直接释放库存或标记退款成功。

### backup-stale
### backup-unavailable
### backup-maintenance
### backup-failed
阅读 [备份与恢复手册](backup-recovery.md)，检查备份任务、目标存储、最近捕获时间和维护 checkpoint。需要恢复原服务时使用已有 `backup.py resume`；巡检不会自动恢复或更改状态。

### restore-failed
最近恢复演练失败或其成功证据不可读。检查 `last-restore-failure.json` 中的固定阶段/错误代码，修复备份、数据库版本或恢复资源问题后重新执行 `backup.py restore-check`。新备份上传成功不能代替数据库恢复比对；告警只在后续实际恢复通过后解除。

## 独立验收

```sh
python scripts/verify_operations_monitor.py --report /tmp/yanhuo-monitor-unique/report.json
```

该入口运行主机工具回归、临时 RSA 证书的回环 HTTPS 接收器，并让每次投递由新 Python 进程运行。覆盖异常通知、503 失败、重启后复用事件 ID、退避、不可信证书、拒绝重定向、恢复通知、持续异常提醒、状态损坏保留和隐私字段检查。重试间隔使用测试程序逻辑时钟；生产命令没有故障开关。该测试中的业务检查输入为合成数据，实际容器链路由完整部署验收入口补充。

真实主机、生产 webhook 接收人、通道可达性和主机外失联探测仍为待验收；本地测试通过不替代这些条件。
