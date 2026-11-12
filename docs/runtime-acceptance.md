# 隔离运行验收入口

这套入口验证单校园试点前可以在本机完成的工程条件。它不会开通真实收款，也不使用开发数据库、上传目录、5183／8087 或参考项目的端口。结果以每次运行自己的 `report.json` 为准；安装了工具或添加了 CI 配置不代表验收通过。

## 运行

Linux／WSL 需要 Python 3.10+、Git、Docker Engine、Compose v2+、restic、OpenSSH 客户端和 OpenSSL。主机 Python 还需安装 `backend/requirements.txt` 中固定版本的 `cryptography`，用于 HTTPS 告警接收器测试（CI 安装同一 requirements 文件）。Docker 必须可由当前用户调用；不要为了运行本工具把整个 Docker socket 改成公开可写。

```sh
python3 scripts/verify_runtime.py --full-regression
```

Windows 使用现有 Ubuntu WSL（默认 `Ubuntu-24.04`）：

```powershell
.\scripts\verify-runtime.ps1 -FullRegression
```

默认执行全部阶段。`--stages deployment` 只做部署、HTTPS、工作进程恢复和巡检；`--stages acceptance` 在此基础上增加库存与通知测试；不带 `--full-regression` 时只运行新增 PostgreSQL 运行验收测试，带上则运行整个 `market` 后端回归。可用 `--evidence-dir` 指定尚不存在的证据目录。不要重复使用旧目录。

Linux 依赖示例：`sudo apt-get install restic openssh-client openssl`。PowerShell 包装器只调用 WSL 中的同一 Python 入口，不维护另一份验证逻辑。

## 隔离与可核查结果

- 随机 Compose 项目名、临时数据库和媒体卷；从工作区复制源码到 Linux 临时目录构建，排除开发数据、`.env` 和私钥文件。保存逐文件 SHA-256、Git 版本和镜像 ID，未提交修改也进入本次源码快照。
- 后端与数据库仅在内部网络连接；只有 Caddy 的 HTTPS 随机端口绑定 `127.0.0.1`。Caddy 为 localhost 生成临时 CA，只有测试客户端显式信任它，不修改主机证书信任。
- 注入实际失败的测试迁移，检查 API、回调及三个 worker 均未启动。移除只读测试挂载后，使用原有 release 命令完成正常发布。
- 通过 HTTPS 实际读取 SPA 与深链接、登录、构建产物、Django 静态文件、上传并读回合成图片；未配置支付的通知路由应由业务入口拒绝。
- 从已核验归属的容器内终止 Python worker，验证相同容器自动重启、每个心跳晚于终止时刻。随后让原待处理订单到期，核对库存仅返还一份和单条审计，再重复执行确认没有二次返还。通知测试另执行独立进程的实际强制终止和自然租约到期。
- 主机巡检面对运行中的 worker，仍必须识别资金待核、通知死信和冲突；业务恢复后问题消失。真实 HTTPS 接收器独立验证持久投递、失败重试及恢复通知。巡检自身只读，注入数据仅来自测试程序。
- 备份通过真实 `pg_dump`、暂停写入、restic、SFTP、文件校验、隔离 PostGIS 和 `pg_restore` 完成。合成资金历史、上传媒体及虚拟支付配置纳入内容比对。SFTP 位于同机隔离容器，**不能作为真实异机灾备证据**。
- 注入备份失败、损坏文件和告警失败，核查原 writer 容器恢复、持久失败记录与恢复临时资源清理。
- 真实恢复失败后再做一次完整备份，确认恢复失败告警仍保留；实际恢复并比对成功后才解除。各阶段分别保留巡检状态和子进程日志，普通备份成功不能代替恢复能力验收。

库存测试是 **1,000 次服务层创建请求、1,000 个用户、最多 20 个并发线程**，使用真实 PostgreSQL 事务争抢 50 份。它不是 1,000 路同时发起的 HTTP 压测。通知是实际 HTTP 到独立 Waitress 进程，使用临时 RSA 与 AES-GCM，正常负载的 100 次、2 并发独立于 100 次突发重放和过载统计；任何正常请求失败都会使该验收失败，快速失败不能改善 p95。

## 失败与清理

每步输出写入独立证据目录，超时也保存已产生的日志。退出前先记录 Compose 状态和日志，再按随机 run 标签查找资源，逐项核验项目、目录和容器 ID；只删除本次容器、卷和网络，镜像标签也须与记录的 image ID 一致。不会调用全局 prune 或按进程名清理。证据不含备份密钥、通知测试私钥或顾客信息。

容器级故障暂停点、临时密钥、故意损坏文件均只存在于测试程序。生产没有故障开关。SIGTERM／Ctrl+C 会进入诊断和清理；主机断电或对验收控制进程使用 SIGKILL 无法保证执行 finally，应按本次项目和 run 标签人工核查残留，不可清空 Docker 全部资源。

## CI 与边界

`.github/workflows/verify.yml` 的部署 job 使用完全相同入口；后端 job 跑全 PostgreSQL 回归；浏览器 job 独立保存首次回归与弱网三次重复运行的日志、截图、trace。新增配置只有在远端实际运行之后，才可标为远端 CI 通过。

最新结果和历史证据统一见 [运行验收证据索引](runtime-evidence-20261003.md)。生产巡检安装见 [巡检与告警](operations-monitor.md)，备份配置见 [备份与恢复](backup-recovery.md)。真实服务器、域名、商户支付、异机备份、告警接收人、Android／iPhone／微信实机及校园现场 48 小时观察分别待验收。
