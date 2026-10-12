# 单机试点发布与 48 小时观察

本文是部署运行手册，不表示生产已验收。当前本地端口仍为前端 5183、后端 8087，`start-dev.ps1 -Lan` 不变。发布使用独立 Linux 主机、真实 HTTPS 域名和单独数据库；不能导入开发示例数据库。

## 自动验证与发布顺序

`.github/workflows/verify.yml` 在 PR、main/master push 和手动运行时检查：PostGIS 17 全量后端测试、迁移一致性、前端类型/生产构建、Vitest 契约测试、Chromium 回归、WebKit smoke、独立 SQLite/media 的真实 API 浏览器流程、生产镜像及 Compose 启动、依赖审计。结果和失败 trace 保存 14 天。远端工作流未实际运行前只能记为“已配置”。WebKit 自动化不代替 iPhone/微信内核验收。

1. 准备随机密钥、已指向主机的域名、仓库外 `/etc/yanhuo/production.env`；数据库、后端、回调及 worker 均不公开端口。所有命令在本项目根目录执行，显式使用同一个 `--project-name yanhuo`，避免误操作其他项目。
2. 记录代码版本与镜像标识；暂停新单，安排已有订单值守。先完成[备份与隔离恢复](backup-recovery.md)，确认原商户支付密钥可恢复。
3. 按下列命令停止本项目写入进程、构建并启动新版本。`release` 单次容器只执行迁移与静态收集；后端、回调和全部 worker 都依赖其成功完成。不要跳过依赖启动新版本，也不要让旧 worker 在迁移期间继续写库。

```bash
# 支付启用或仍有旧在途交易时，数组追加 -f compose.payments.yaml；持续保留密钥挂载。
dc=(docker compose --project-name yanhuo --env-file /etc/yanhuo/production.env -f compose.yaml)
# dc+=(-f compose.payments.yaml)
"${dc[@]}" config --quiet
"${dc[@]}" build
"${dc[@]}" stop backend callback worker payment_worker notification_worker
# 只删除已停止的单次 release 容器，不删除任何数据卷。
"${dc[@]}" rm --force release
"${dc[@]}" up --detach --wait --wait-timeout 180
"${dc[@]}" exec -T backend python manage.py migrate --check
"${dc[@]}" exec -T backend python manage.py check --deploy --fail-level WARNING
"${dc[@]}" exec -T backend python manage.py check_operations
```

4. 若迁移失败，保持应用停写，查看 `release` 日志并修复；不能让旧代码绕过失败门控继续运行。完整启动通过后检查 Caddy、三个 worker、回调健康及公网 `/api/v1/health`，再按商户逐步恢复新单。
5. 以真实学生/商家测试账号双设备完成自取、到摊付款、库存竞争、价格变化、位置过期停单和权限隔离；不把虚拟付款当实付。回滚优先用兼容当前 schema 的修正版。整库恢复必须停写，保存事故快照并核对备份之后真实微信流水，不能直接退回旧 schema 或覆盖线上库。

已有停单/资金限制保持有效；校园交接点配送代码准备包含在本轮，逐商户通过预付款、交接点、异常处理和收餐的实机验收后才开放。JSAPI、骑手端或跨摊合单不在首轮范围内。

## 回调隔离与观测

Caddy `/api/v1/payments/wechat/notify/*` 仅路由到 `callback:8088`，该路径限制 2 MiB。回调进程使用两个 Waitress 线程、32 个连接上限及应用并发上限；其他 API 继续使用后端独立的 8 个线程和 6 MiB 限制。两者都仅信任受控 Caddy 提供的 HTTPS 头。`/callback-health` 是容器内部静态健康探针，不公开代理，不证明数据库或业务处理成功。

只有验签通过且通知持久落库后返回 204；配置或数据库暂不可用时返回失败让微信重试。`process_payment_notifications --loop` 异步推进业务状态。不能把“204 已收到”解释为订单已履约或已退款。

同时监控 `check_operations` 全局结果与各 worker 心跳：通知 `pending/dead/conflicts/oldest_pending_seconds/overdue`，付款/退款积压、人工核款及备份新鲜度。只看 worker 存活不能发现死信。将非零退出或 unhealthy 接入真实告警渠道，并验证一次值守人实际收到的演练告警。

现有[主机巡检工具和 systemd 定时配置](operations-monitor.md)每 60 秒执行该全局检查，保存异常、提醒、恢复与投递重试状态。迁移失败阻断、完整 HTTPS 链路、worker 重启和同机隔离备份恢复可先用[共用验收入口](runtime-acceptance.md)重复验证；它不替代目标服务器和实际值守人的验收。

经授权运营核验原交易、事件和幂等记录后，可执行：

```bash
python manage.py retry_payment_notification <通知UUID> --reason '已核对原交易，重试业务处理'
# 冲突需独立审计；此动作不重排队、不改变资金事实。
python manage.py retry_payment_notification <通知UUID> --reason '已核对原通知与冲突来源' --acknowledge-conflict
```

原通知、业务尝试及审计记录保留；普通重试不会自动清除冲突计数。已接收通知的业务处理不依赖重新验签或当前网关配置，仍需维护旧交易查询所需的原商户密钥。

## CSP 与地图验收

Caddy 对 SPA 和深链接强制 `frame-ancestors 'none'`、`object-src 'none'`、`base-uri 'self'`，并发送 `X-Frame-Options: DENY`。资源 CSP 目前是 `Content-Security-Policy-Report-Only`：浏览器控制台显示违规，不采集可能含取餐码/支付地址的完整报告 URL。该策略尚未强制限制脚本、连接或图片，不能写成“完整 CSP 已落地”。

正式高德 key、域名白名单配置后，在 Chromium、WebKit 和实体手机逐页检查地图加载、定位、导航、二维码、上传预览、付款跳转和动态样式；按实际必要域名收窄并处理违规，然后单独发布强制资源 CSP。禁止为消除告警直接加入任意 `https:` 或全局 `unsafe-eval`。未配置地图时列表和手动校园选择必须保持可用。

## 真实开通与 48 小时观察

| 阶段 | 必须完成 | 记录/停止条件 |
|---|---|---|
| 开放前 | 经营主体/逐商户号与 AppID、Native/H5 权限、HTTPS 回调、原密钥保管、备份恢复、真实告警、Android+iPhone 操作 | 每项填写负责人、时间、脱敏证据；未通过商户保持线上支付关闭 |
| 0–2 小时 | 1–2 家已核验商户、少量邀请学生、有值守接单时段；先自取/到摊付款，再逐商户已授权小额实付 | 学生付款→主动查单/回调→商家接单→出餐→取餐核销；失败/弱网按原键恢复 |
| 2–24 小时 | Native/H5 跳转返回、全额退款、关闭退款处置、通知重放、手机锁屏/后台恢复与双设备同步；准备开配送的商户另验真实预付、校园交接点、拒单/超时退款、异常交接及一次性收餐 | 未明资金、通知死信、持续缺单、过期位置可下单或权限越界时暂停受影响商户新单；配送未验收商户保持关闭 |
| 24–48 小时 | 每商户与微信次日账单逐笔核对；查看真实履约数据和人工观察；复查备份/告警 | 未解决差异继续追踪；不得以模拟测试或本地金额差替代到账证明 |
| 48 小时复盘 | 根据业务会话统计开摊/接单/拒单/完成/超时/位置过期及用户任务完成情况 | 满足目标再扩大，未通过则保持小范围值守试点 |

真实设备记录至少包括：Android Chrome、iPhone Safari、微信内浏览器的系统/版本、日期、测试人，360/390 宽度键盘遮挡、44px 触控、定位拒绝、断网提交恢复、身份切换与付款回跳。微信内未实现 JSAPI，入口说明应引导外部浏览器或到摊付款。通知在后台/锁屏/页面关闭的可靠性需真实测量；未验证前不能作为唯一接单渠道。

参考：[Compose 启动依赖官方说明](https://docs.docker.com/compose/how-tos/startup-order/)。
