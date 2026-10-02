# 微信支付网关依据与边界

核查时间：2026-09-26。这里只记录网关协议与来源；商户开通、部署及业务状态流程另见项目的支付接入说明。

## 已实现的协议范围

`backend/market/wechatpay.py` 提供普通直连商户的微信支付 API v3 适配：Native 下单、H5 下单、商户订单号查单、关单、全额退款申请、退款查询、支付与退款通知验签解密。

- 默认只配置 `native`。H5 必须由运营明确加入账户 `channels`，并已在微信侧开通产品及配置支付域名。
- JSAPI 依赖公众号/应用、网页授权和相应 OpenID，本轮没有这些条件，适配器不会伪造或开放 JSAPI。
- 每个独立经营主体绑定自己的直连收款账户。网关不自动选择收款主体，不提供多商家共用平台商户号代收及分账；服务商/子商户模式留待对应资质和产品接入完成后单独实施。
- `enabled=False` 阻止发起新支付，但保留已发生交易的查单、关单、退款与通知处理能力。
- 没有商户配置时不调用真实网关，不创建“模拟已支付”结果。隔离测试使用临时生成的密钥与替代网络传输。

## 官方来源

| 主题 | 官方依据 | 对实现的影响 |
| --- | --- | --- |
| 请求与应答签名 | [API v3 签名和验签总述](https://pay.wechatpay.cn/doc/v3/merchant/4012365342)、[带 Body 参数如何签名](https://pay.wechatpay.cn/doc/v3/merchant/4012365336) | 使用商户私钥生成 RSA-SHA256 请求签名；成功应答也必须验证，不只验证回调。 |
| H5 下单 | [H5 下单接口](https://pay.wechatpay.cn/doc/v3/merchant/4012791834) | 金额以整数分传递；填写真实设备 IP 和 `h5_info.type=Wap`；支付截止时间至少还有 60 秒。到期不等于已关单。 |
| 支付成功通知 | [支付成功回调通知](https://pay.wechatpay.cn/doc/v3/merchant/4012791861) | 校验原始正文签名，再用 API v3 密钥进行 AES-256-GCM 解密；重复通知需要业务层幂等处理，不能只依赖回调。 |
| 查单 | [商户订单号查询订单](https://pay.wechatpay.cn/doc/v3/merchant/4012791859) | 服务端查单用于弥补回调延迟、丢失及前端返回不可靠的问题。 |
| 关单 | [关闭订单](https://pay.wechatpay.cn/doc/v3/merchant/4012791860) | 调用专门关单接口；签名可信的空正文 204 才可视为关单请求成功。网络超时不能当作已关闭。 |
| 退款 | [退款申请](https://pay.wechatpay.cn/doc/v3/merchant/4012791862)、[查询单笔退款](https://pay.wechatpay.cn/doc/v3/merchant/4012791863) | 同一退款尝试始终复用 `out_refund_no`。受理 `PROCESSING` 不等于退款成功，最终状态由通知或查单确认。 |
| JSAPI 场景 | [JSAPI 开发指引](https://pay.wechatpay.cn/doc/v3/merchant/4012791870) | 微信内网页调用支付能力需要对应配置，前端完成回调也必须交服务端核实。 |
| 多商户服务商模式 | [服务商 Native 下单](https://pay.wechatpay.cn/doc/v3/partner/4012738659) | `sp_appid`、`sp_mchid` 与 `sub_mchid` 区分服务商及实际收款商户，不能用普通商户号字段替代。 |
| 密码学实现库 | [cryptography 官方 PyPI](https://pypi.org/project/cryptography/50.0.1/) | 固定 `cryptography==50.0.1`，使用其 RSA 与 AESGCM 实现，不自行实现密码算法。 |

## 服务端契约

`WechatPayClient(config)` 只接受受控服务器配置，不接受网页传入的凭据、账户标识或支付金额。必要字段：

```text
enabled: bool                       # 是否允许新支付
mode: "direct"                     # 省略时为 direct，其他模式拒绝
appid: string
mchid: string
serial_no: string                   # 商户 API 证书序列号
private_key_path: string            # 服务器上的 PEM 私钥文件
api_v3_key: string                  # 32 字节；可由上层从受控密钥文件读取
payment_public_keys: {id: path}     # 微信支付公钥 ID → PEM 公钥文件；允许轮换时并存
notify_url: https URL              # 支付通知地址
refund_notify_url: https URL       # 退款通知地址，可与支付通知地址相同
channels: ["native"]               # 可明确加入 h5，不支持 jsapi
timeout: 8                         # 可选，范围 1～15 秒
```

不在仓库、浏览器、日志或管理表单公开私钥/API v3 密钥。公钥从商户平台可信渠道取得并按 ID 固定，不信任回调自己携带的新公钥地址。网关仅连接固定官方 HTTPS API 域名，拒绝跟随重定向。

所有接口返回已验签的微信 JSON，保留原 `trade_state`/`status`。`close_payment` 成功返回空对象。`verify_notification(headers, raw_body)` 返回 `{id, event_type, resource, create_time}`，其中 `resource` 已解密；网关会核对 `mchid` 及存在时的 `appid`。订单号、金额、币种、交易号唯一性、历史账户快照、重复通知和订单状态推进由业务层再次检查。

`GatewayError` 含 `code`、`safe_message`、`retryable`、`outcome_unknown`、`status_code`。超时、无签名/错误签名、无效应答均保留“结果未知”，不能转换为支付失败、恢复线下收款或释放支付占用。网关不自动重试；业务层需要先查单，再使用已保存的原始单号恢复。参数错误和配置未完成才标记本次没有向网关发起交易。

## 安全验证

`market.test_wechatpay` 的 17 项测试全部使用生成的临时密钥和被替代的 HTTP 传输。覆盖请求签名正文、签名查询路径、应答篡改、公钥 ID 不匹配、签名探测流量、过期/未来时间戳、AES-GCM 篡改、跨账户通知、重复 JSON 字段、签名空正文关单、超时未知态、H5 URL 检查、退款受理态、退款通知与未配置禁用。

这些测试验证代码行为，不代表商户产品审核通过、外网回调连通或真实支付联调完成。
