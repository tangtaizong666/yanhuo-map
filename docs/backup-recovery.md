# 加密异机备份与隔离恢复

目标为 **RPO 1 小时、RTO 2 小时**，这是试点目标而非已经测得的保证。单机数据库/媒体 named volumes 保留；本机卷不承担灾备。真实异机地址、凭据、告警接收人和恢复主机尚需部署方配置并完成演练。

## 配置与运行

主机需 Python 3.10+、Docker Compose v2、restic（支持 `restore --verify`）。将 `deploy/backup.env.example` 的配置放在仓库外 `/etc/yanhuo/backup.env`，权限 0600。运行环境文件、restic 密码、支付密钥和告警 URL 文件均由管理员保管，不能进入 Git 或日志。restic 仓库密码的离线副本应与备份仓库分开保管，否则主机丢失后无法解密。

必须设置明确项目名/目录、生产环境文件、独立状态目录、预初始化的加密远端 restic 仓库和失败告警 HTTPS URL。脚本拒绝本地文件仓库、未确认的异机目标、缺失配置和不安全文件权限；不自动创建仓库，不回退成本地“成功备份”。云/SFTP 的访问凭据使用 restic 对应环境配置或受控凭据文件。

只要仍有任何真实旧付款/退款需查询，`BACKUP_PAYMENTS_CONFIGURED=true`，并配置仓库外 `WECHAT_PAY_SECRETS_DIR`。即使新支付已关闭，也不能漏掉历史商户配置、私钥、API v3 密钥和验签公钥。

启用 `BACKUP_MAINTENANCE_APPROVED=true` 前必须安排短维护窗口。此单机方案为了数据库与媒体一致，会临时停止本项目原来运行的 backend、callback 和三个 worker；只在采集完成后立即恢复这些容器，上传和校验不延长停写。Caddy 继续运行，采集期间动态请求可能失败，学生/商家按原请求键恢复，微信回调依靠重试。不能用于要求零停机的部署；扩大试点前应改为数据库/存储一致性快照方案。

脚本同时核对 Compose `project` 和 `working_dir` 标签，只处理本次枚举的容器 ID；不按可执行程序名、端口或名称前缀停进程。发现一个服务有多个实例即拒绝本方案，避免只停一部分写入。工作开发服务 5183/8087 不在其作用范围。

由 systemd 读取环境文件执行 `scripts/backup.py`：

- `backup`：一致性 `pg_dump`、媒体完整目录、运行环境和支付密钥采集；表内容哈希和逐文件 manifest；restic 加密上传后下载同一快照逐文件校验，`restic check` 后按保留策略清理旧代次。成功时间和采集时间分别记录，RPO 以采集时间计算。
- `status`：超过 1 小时无有效采集、无历史成功、时钟明显倒退或未完成维护检查点时非零退出并告警。
- `restore-check --snapshot <完整快照ID或latest>`：只选本项目快照；验证解密文件哈希，在唯一标签的 `--network none` PostGIS 容器中真实 `pg_restore`，逐表比较内容哈希；不启动业务应用，不连接微信或接收公开回调，结束仅删除自己创建的容器和卷。核对恢复时长是否低于 2 小时。
- `resume`：若主机崩溃/进程超时遗留 `maintenance.json`，经值守人核对后恢复检查点中拥有正确标签的原容器。检查点清理前后续备份不会继续停服。

`last-success.json` 表示加密文件回传校验完成，**不等于 PostgreSQL 恢复演练通过**；后者独立记在 `last-restore.json`。两种记录都不含密钥、库中行内容或远端 URL。失败命令只记录阶段和固定错误代码，外部程序原始输出不进公开日志；告警发送失败也以非零结果记录。告警渠道本身仍需独立可用性监测。

恢复失败还写入独立的 `last-restore-failure.json`，保留告警投递结果；普通备份、状态检查或后续备份失败不能覆盖它。主机巡检兼容旧共享失败记录，但仅用之后完成的数据库/文件恢复比对解除 `restore_failed`，不以较新的 `last-success.json` 宣布恢复能力恢复。保留失败文件供追溯，不手工删除来消除告警。

## 调度与保留

审核 `deploy/systemd/` 中的绝对路径，安装到目标主机后先人工运行并验证，再启用三个 timer：

| 任务 | 默认调度 | 目的 |
|---|---|---|
| `yanhuo-backup.timer` | 每 30 分钟，最多随机延迟 30 秒 | 为 1 小时 RPO 留一次失败重试余量 |
| `yanhuo-backup-status.timer` | 每 10 分钟 | 检查备份过期、残留维护状态并告警 |
| `yanhuo-restore-check.timer` | 每月 1 日 03:15 | 无网关网络的完整恢复演练；发布迁移前另行执行 |

默认保留 48 个小时代次、14 个日代次、8 个周代次、12 个月代次；`BACKUP_KEEP_*` 可调整且必须大于零。清理仅限当前项目 host/tag。`backup` 与恢复演练有互斥锁，不能并发进入维护/恢复；调度冲突会显式失败并通知。持续超过两小时的恢复需人工调整资源，不能简单延长目标然后记为达标。

示例启用命令由部署负责人在真实主机执行，本地开发不运行：

```bash
install -m 0644 deploy/systemd/yanhuo-* /etc/systemd/system/
systemctl daemon-reload
systemctl start yanhuo-backup.service
systemctl start yanhuo-restore-check.service
# 确认两次实际结果、加密密钥异地托管和告警接收后启用。
systemctl enable --now yanhuo-backup.timer yanhuo-backup-status.timer yanhuo-restore-check.timer
```

## 上线验收记录

本机可先执行[隔离运行验收](runtime-acceptance.md)。该入口真实暂停专用测试栈写入，执行 `pg_dump`、SFTP/restic 加密存储、媒体和虚拟支付配置校验、独立 PostGIS 恢复及资金历史内容比对，并注入 dump、文件和告警故障。数据库身份来自已验证归属的 DB 容器，不再假定库名固定；失败及告警投递结果持久记录在 `last-failure.json`，供全局巡检读取。具体本批结果见[证据索引](runtime-evidence-20261003.md)。同机 SFTP 演练不能替代真实异机目的地、密钥保管或灾难后新主机恢复验收。

逐项填写目标主机、负责人、时间、证据位置和结果：全量加密上传→目标主机故障后的新主机还原→数据库内容/库存/订单/历史退款/幂等记录一致→商家原图可读→支付配置可解密且不发起付款→恢复耗时→一次故意失败告警实际送达→失联后 RPO 超时告警。真实密钥只由授权操作人验证，报告不可抄录。

生产事故恢复不能直接把旧快照覆盖线上库。先暂停新单并保存事故快照，隔离还原、核对备份时间之后微信账单与通知，再决定切换；付款和退款保持关闭，避免重复扣款、退款或错误核销。

参考：[restic 备份](https://restic.readthedocs.io/en/stable/040_backup.html)、[restic 命令手册](https://restic.readthedocs.io/en/stable/manual_rest.html)。
