# 烟火地图 API

Django 5.2 LTS + Django REST Framework。开发环境使用 SQLite；生产环境只接受 PostgreSQL，迁移启用 PostGIS 并建立摊位位置的 geography 生成列/GiST 索引。GCJ-02 坐标与高德一致，页面距离为近似直线距离，不作为步行路线距离。

## 本地启动（PowerShell）

```powershell
cd C:\my_pycharm\流动摊贩\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8087
```

另一个终端运行 `.\.venv\Scripts\python.exe manage.py expire_orders --loop`。默认每 15 秒分批处理待接单超时、记录过期摊位和超过一小时未领取的订单；读取接口不清理订单，下单仅清理目标摊位相关到期预约。不会把未领取订单自动完成。支付环境另需 `reconcile_payments --loop`；两者通过 `check_operations` 监测心跳。

本地后端使用 8087 端口，前端使用 5183；8087 用于避开电脑上其他项目已有的 8000 端口服务。优先使用项目根目录的启动脚本同时启动前端、后端和超时处理进程。Docker 容器内部仍使用 8000 端口。

演示账号：`student`、`vendor`、`admin`，密码均为 `demo12345`。仅显式 `seed_demo` 创建；重复执行不会重置已有密码、库存、订单或位置确认时间。示例商户位置、资质均非真实运营数据。开发数据库不会在生产模式自动使用。

显式模拟付款与外卖使用 `SERVICES_SIMULATION_ENABLED=true`，仅允许非生产 `DEMO_MODE` 下的示例摊位；订单、付款和退款保存各自模式快照，模拟不调用真实微信网关。普通商家只需使用店铺设置的服务开关，开发准备、模拟接口及后续真实接入见[模拟体验指南](../docs/simulation-guide.md)，实际验证见[模拟验证记录](../docs/simulation-verification.md)。

运营后台位于 `/admin/`。登录 admin 后核验商户档案、启用摊位交易资格、设置位置过期/接单超时阈值。订单和日志只读，状态变更通过商家 API 操作，避免跳过库存、付款和核销规则。

配置参见 `.env.example`：值需要由终端、部署平台或进程管理器注入环境；不会隐式读取 `.env`。开发 CSRF 默认允许 5173 和 5183 两个本地端口；手机局域网调试需要把实际 IP 加入 `DJANGO_ALLOWED_HOSTS` 及 `CSRF_TRUSTED_ORIGINS`。不要在生产配置通配主机或任意来源。

## 接口约定

### 试点安全与分页接口（2026-10-03）

订单响应新增 `financial_hold_reason`（无资金限制时空字符串）和 `allowed_actions`。动作列表同时结合当前状态与请求用户权限，供页面控制按钮；每次服务端写操作仍独立检查操作者、状态、款项、库存与模式。只读运营不会拿到写入或退款能力。款项未核清时隐藏取餐码并禁止核销，退款 CLOSED 不再能恢复正常履约。原 `refund` 提供当前未结案尝试或最新摘要；`refunds` 保留尝试历史及 `resolved_at`。原付款、退款编号和模式快照不覆盖。

`GET /orders` 与 `/merchant/orders?stall=<id>` 可加 `pagination=cursor`，返回 `{results,next,counts}`。`filter` 为 `all|active|followup|attention|completed|cancelled`，`page_size` 默认 30、范围 1–100。`next` 是签名的不透明游标或 null，下一次以 `cursor=<next>` 传回；游标绑定当前账号、摊位与筛选，失效返回 400 `invalid_cursor`。顺序为创建时间及 UUID 倒序。`counts` 包含上述六项及 `reviewed`，按当前授权账号/摊位完整范围统计，不随游标截断。

`attention` 包括活动履约订单及任何资金待处理记录，商家端另含履约中的配送异常；已取消、已完成但退款关闭未结案的旧订单仍会返回。新前端只轮询此集合并读取全部活动分页；历史列表显式翻页。未加 `pagination=cursor` 的旧客户端暂时继续收到原数组，兼容入口不代表推荐全量轮询。只读接口不再执行全站过期清理。

新增商品 `POST /merchant/stalls/:id/products` 接受 `idempotency_key`（8–128 字符）：首次 201，同摊位同键同内容重放 200 并返回已有商品；同键不同内容 409 `idempotency_conflict`，原商品已删除 409 `idempotency_resource_gone`。旧无键请求暂保留。前端把结果未知的原键与原内容保存在当前账号草稿中，未核实前不换键创建。

权限策略、迁移顺序、worker 监测、备份恢复、异常核验和逐商户真实开通见[试点运维指南](../docs/pilot-operations.md)。后台和 API 登录共用数据库原子失败额度；所有游客限流统一可信客户端 IP 边界。HTTP 请求体上限 6 MiB、累计上传图片 5 MiB，图片仅解析 JPEG/PNG/WebP。

所有路径前缀 `/api/v1`，无末尾斜杠。普通列表和旧版订单调用返回数组；新订单分页结构见上文。金额为整数分；时间为带时区 ISO 8601；订单 ID 为 UUID。

- `GET /config` 返回品牌、演示标志、区域列表、当前用户和地图配置。`GET /auth/me` 返回用户对象或 `null`。
- 首次写操作前 `GET /auth/csrf`；使用同源 session cookie，并把 `csrftoken` cookie 放入 `X-CSRFToken` 请求头。登录会轮换 CSRF token，后续写请求应重新读取 cookie。
- `POST /auth/register`（username/password/display_name）、`POST /auth/login`、`POST /auth/logout`；`PATCH /auth/profile`；`POST /auth/password`（old_password/new_password）。注册不收集学号。
- `DELETE /auth/account` 需要 `{password}`；有进行中订单、商户、运营账号拒绝注销。普通账号停用并去标识化，去掉订单联系方式、整单与逐份备注并停用恢复码，保留必要交易历史。正式部署前由运营确定法定保留时间及删除工作流程。
- `GET /stalls?area=&q=&category=&status=&sort=&lat=&lng=`：品类为“小吃/烧烤/饮品/正餐/甜品”；状态支持 open/paused/closed/stale/orderable；排序 recommended/rating/distance。搜索同时匹配摊位及上架商品。没有位置不返回虚构距离，没有评价时 rating 为 null。
- `GET /stalls/:id` 包含商品、近 20 条评价与经营信息；`POST/DELETE /stalls/:id/follow` 返回更新后的摊位；`GET /follows` 获取持久关注。
- `POST /orders` 提交 `{stall_id,items:[{product_id,quantity,expected_price_cents}],idempotency_key,note?,contact_phone?}`；`GET /orders`、`GET /orders/:id`。相同 key+相同请求返回原订单；同 key 不同请求返回 409。客户端在一次提交及其网络重试中保留同一个 key，编辑后生成新 key。
- `POST /orders/:id/cancel`（reason）、`POST /orders/:id/review`（rating/content）。先接单后的取消仅提出申请；已收款不能取消。
- `GET /orders/active-summary` 需要登录，只返回本人的进行中订单数量（pending/preparing/ready/total）及一笔优先提醒订单，待取餐优先、同状态按创建时间排序。响应含 `user_id` 供客户端核对账号，不含取餐码、商品明细、联系方式或备注；使用 `private, no-store`，读取时同样清理过期待接单订单。无进行中订单时 `order: null`。
- `GET /merchant/stalls`、`GET /merchant/orders?stall=`；`POST /merchant/stalls/:id/status`（status/confirm_location/latitude?/longitude?/address?/closes_at?）。更换位置会撤销交易资格，运营重新核验后才能接单。摊位响应的 status 表示有效状态，session_status 表示原始营业会话状态；单纯确认位置应保留 session_status，避免将暂歇摊位意外恢复营业。已到预计收摊时间的会话，确认重新开摊时新建会话且不继承过期收摊时间；显式提交过去的收摊时间仍会拒绝。
- `POST /merchant/stalls/:id/products` 创建商品，允许填写初始 stock；`PATCH /merchant/products/:id` 部分更新 name/description/category/image/price_cents/is_active/sale_paused/taste_options。编辑资料不能修改 stock，库存增量用补货、盘点用独立更正接口。商家列表包含下架商品，公共列表仅展示上架商品。下架保留已下单快照；只改价时不覆盖已预留库存。免费口味字段详见下文。
- `PATCH /merchant/stalls/:id/profile` 更新 name/description/image/prep_minutes/contact_phone/usual_hours，以及下文认摊与接单字段；其他字段拒绝修改。联系电话属于经营主体，旗下摊位共用；资质与交易权限只能由运营核验。
- `POST /merchant/stalls/:id/image` 上传 multipart `file`，上限 5 MB、2000 万像素，接受真实可解码的 JPG/PNG/WebP；重新编码为 JPEG 并清除 EXIF，以随机文件名写入 `MEDIA_ROOT`，返回 `{url}`。`/media/` 开发环境由 Django 提供，生产由 Caddy 读取共享媒体卷。数据库与媒体卷必须一起备份。
- `GET /merchant/reviews?stall=` 查看评价；`POST /merchant/reviews/:id/reply`（content）回复本摊位评价。回复允许更新；content 为空字符串时撤回本人回复，replied_at 置空，用户评价原文及星级不变。操作审计留痕，并通过公共评价的 merchant_reply/replied_at 展示给用户。
- `POST /merchant/orders/:id/action`，自取 action 为 accept/update_prep/reject/ready/confirm_payment/complete/approve_cancel/deny_cancel，配送另有 dispatch/arrive/report_delivery_issue/resolve_delivery_issue。complete 需要 `pickup_code`（8 位）。先确认收款，才能核销；核销一次后不可重用。商家响应隐藏取餐码，学生自己的订单才显示。备餐预估参数及幂等规则详见下文。
- `POST /feedback`（content/contact?）；`POST /events`（type/stall_id?），事件可为 browse/home_view/stall_view/map_view/checkout_view/location_denied/map_error/api_error。
- `GET /merchant/metrics?stall=&days=1|7|30&mode=live|simulation`：默认含今天的近 7 个上海时区自然日，模式默认为 `live`。按订单模式快照区分真实与模拟收款；模拟金额不是实际营收或平台余额。保留浏览次数、创建订单数、完成率等字段，以及 paid_orders/average_order_cents/followers/products_active/products_sold_out/today/series/top_products/recent_payments。收款、客单价、热销商品与收款流水按 paid_at 统计；退款按完成日期另计。orders_completed 与完成率使用“期间创建订单中当前已完成”的同一批次；today/series 的完成数按 completed_at 统计。metric_definitions 提供具体口径，浏览事件不是去重人数，转化率仅供参考。
- `GET /health` 数据库就绪检查。服务端异常记录 `market` 日志，客户端上报 api_error 用于统计。

错误返回 `{detail, code?, errors?...}`。业务冲突 409、验证 400、未登录/无权限 403、不可见资源 404。`price_changed` 附带 products 最新价格，前端必须让用户重新确认。其他主要 code：out_of_stock、stall_unavailable、invalid_transition、invalid_pickup_code、idempotency_conflict。

## 找摊、入驻与日常备货接口（2026-09-29）

迁移 `0008_merchant_onboarding_and_visit_reports` 为既有摊位新增独立认摊照片/说明、未核验地址草稿和接单开关，旧照片、坐标和订单保持原值。`accepting_orders` 默认开启，实际能否下单仍同时检查营业/位置新鲜度/商户资质/接单权限。关闭该开关只停止新订单，既有履约与已预留配送订单的付款继续使用原规则。

- `GET /config` 新增 `stale_minutes` 与 `public_base_url`。公开分享域名由环境变量 `PUBLIC_BASE_URL` 配置；未配置为空，开发环境允许 HTTP，生产要求 HTTPS。只接受 origin，不接受凭证、路径、查询参数或片段。
- `GET /stalls` 的默认、`recommended`、`freshness` 均按“出摊中 → 暂歇 → 状态待确认 → 已收摊”，每组按最近位置确认优先排序。没有在线点单资格的线下摊位不会因此消失。
- 摊位新增 `arrival_note`、`arrival_image`、`accepting_orders`、`order_unavailable_reason`。`PATCH /merchant/stalls/:id/profile` 可修改上述前三项以及 `location_draft_address`。草稿地址只返回商家，绝不替换已核验坐标/地址；位置真正变更继续走原 `/status` 核验规则。认摊照片通过既有图片上传接口上传，与菜品/店铺封面分开存储。暂停/收摊时 `confirm_location` 默认 false，不自动确认位置。
- `GET/PATCH /merchant/application` 返回 `{application: null | {...}}`。PATCH 可创建和修改本人 draft/needs_changes/rejected 资料；字段为 `business_name, stall_name, contact_phone, area_id, category, address_note, description`。`POST /merchant/application/submit` 校验必填项并记录本人确认；已提交/已批准时重复提交不重复建档。账号资料新增 `merchant_application_status`。
- Admin 的“商家入驻申请”允许选择已注册普通账号代录 draft（source=assisted），必须由本人提交确认。审核通过在一个事务内幂等创建**未核验商户、隐藏摊位**；不会赋予展示、交易、线上支付或配送权限，不会用文字地址虚构坐标。“要求补充”“不予通过”操作须先填写审核意见。已提交申请的内容不允许管理员静默修改；需退回本人重新确认。
- 入驻审核动作和服务层均要求 `market.change_merchantapplication` 权限，只有查看权限的员工不能审批。成功补货的同键重放不要求商品仍存在：只返回现存商品与 `replayed:true`，不会再次增加库存；新批次仍严格校验商品归属与存在性。
- `POST /merchant/stalls/:id/restock` 接收 `{idempotency_key,items:[{product_id,quantity}]}`，quantity 为正增量。整批按摊位→商品 ID 顺序加锁校验，只增加当前库存，不改上下架/价格。返回 `{products,replayed}`；同 key 同内容重试返回最新商品且不重复加量，不同内容返回 409；取消订单依旧只释放原预留数量一次。
- `POST /feedback` 兼容旧 `{content,contact}`。现场问题支持 kind=`not_found|wrong_location|mismatch`、`stall_id`、`idempotency_key`、`location_snapshot:{address,latitude,longitude,last_confirmed_at}`，content/contact 可空。游客可提交，须 CSRF，按账号或匿名 IP 限制 20 次/小时；同 key 同内容去重，不同内容冲突。位置快照是**用户当时看到的记录，未经核实**；不会自动下架或改写营业状态。
- `GET /merchant/stalls/:id/location-reports` 返回 `{unresolved_count,reports}`，报告优先未处理，最多最近 20 条，含 id/kind/location_snapshot/snapshot_source/created_at/resolved。绝不返回联系人、账号或自由说明（说明中也可能包含个人信息）。商家重新确认位置不会把报告自动标记为已解决；仅运营在 Admin 核实处理。
- `POST /events` 新增 share_click/share_open/qr_open/route_click/reorder。来源可用顶层 `source` 或 `metadata.source`，两者必须一致；仅允许 `home,home_recent,search,map,follow,recent_order,stall_detail,stall_qr,share_link,merchant_preview`，未知字段拒绝。来源是客户端声称的粗粒度入口，不等于传播人数或真实扫码量，不存原始网址/位置/个人信息。
- `GET /orders/recent-completed` 仅返回当前账号最近 3 个完整已完成订单，用于复用现有再来一单流程；菜单、价格和库存仍需重新确认。
- `/merchant/metrics` 新增 `completed_customer_count`、`returning_customer_count`、`returning_customer_rate`。按所选模式和实际完成时间统计；回头客为本期完成订单且此前曾在同一摊位完成同模式订单的用户，按用户去重。无完成用户时比例为 null。履约后退款不抹去已履约事实，不应称作净成交或留存率。
- `/merchant/metrics` 的 `source_counts` 为 `{source,event_type,count}` 数组，仅计入白名单入口来源。按摊位与时间范围汇总事件次数；不按用户去重或订单模式分开，不等于真实扫码人数或转化归因。

新增行为与权限测试在 `market/test_operations.py`。隔离 PostgreSQL 验证可使用 `tools/verify_operations.py --bin <现有便携PostgreSQL的bin目录> market.test_operations market.tests.PostgreSQLConcurrencyTests`；只读复用运行时，新建临时 data，自动停止自己的数据库，不 seed/reset、不触及业务库。2026-09-29 本轮 PostgreSQL 28 项通过，报告 `test-results/operations-20260929-postgres.txt`。

## 逐份口味、备餐预估与账号恢复（2026-09-29）

迁移 `0009_tastes_and_preparation` 新增商品口味、逐份订单快照及备餐预估，`0010_account_recovery_and_hours` 新增账号恢复码记录和计划营业说明。旧商品默认无口味选项、旧订单逐份信息为 `[]`、出餐预估为 `null`；不会反推或编造历史用户要求和预计时间。

### 免费口味与逐份备注

- 商品 `taste_options` 为 `[{name,choices:[string]}]`，默认 `[]`。最多 3 组，每组 1–8 个选项；组名和选项均为 1–20 字的文字，去除首尾空白后拒绝重名。所有选项均免费、可不选；不接受加价等额外字段。商家创建/修改商品和公共商品响应使用同一字段。
- `POST /orders` 中的每个 items 元素可选传 `portions:[{options:{组名:选项},note}]`；传入时长度必须严格等于 quantity，每份 options 默认 `{}`、note 默认空字符串且不超过 100 字。一个商品仍只占一条订单项，总数量用于价格计算和库存扣减。
- 后端在同一事务内按当前菜单验证所选组名/值，失效选项返回 HTTP 409、`code=tastes_changed` 和 `product_id`。客户端应重新获取菜单并让用户重选，不应静默删除用户要求或绕过原有价格/库存复核。
- `Order.items[].portions` 保存下单时的逐份快照，之后修改菜单不覆盖历史记录。完全空白的逐份信息与省略 portions 使用相同订单请求指纹，兼容旧客户端网络重试；成功请求在菜单变化后的同 key 重试仍返回原订单。修改了非空口味或备注后必须使用新 key，不能将它当作原请求重试。

### 接单与备餐预估

`POST /merchant/orders/:id/action` 的备餐动作如下；原有不带新字段的接单请求继续有效。

| action | 请求字段 | 行为 |
|---|---|---|
| accept | `prep_minutes?`、`idempotency_key?` | 接单时生成出餐预估；分钟数省略时使用店铺默认值。 |
| update_prep | `prep_minutes`、`reason`、`idempotency_key` | 仅制作中、无待处理取消申请且支付状态允许时可用；reason 为必填调整说明。 |

prep_minutes 为 1–180 的整数，reason 不超过 200 字，idempotency_key 长度为 8–128。分钟数表示从**本次服务端确认起还需多久**。订单返回 `estimated_ready_at`、`prep_updated_at`、`prep_delay_reason`；商家预估不是保证出餐时间，逾时不会自动标记出餐、付款或完成，也不替代配送预计送达时间。

有 key 的 accept/update_prep 按订单持久化幂等记录：同 key、同动作及参数返回当前订单，不重新计算时间；同 key 不同动作或参数返回 409 `idempotency_conflict`。网络结果不明时保留原动作、参数及 key 重试；只有确认要发起新的调整才更换 key。无 key 的旧版 accept 重复提交仍受原状态门限制，不能让已接单订单重新计时。更新预估不会改变收款、取餐码或库存规则。

`Stall.usual_hours` 是商家选填的计划营业说明，最长 100 字，可通过 profile 接口修改。它不表示现在已经出摊，不更新位置确认、不自动开收摊，也不会覆盖 closes_at；学生端应同时展示当前营业状态和商家确认时间。

### 一次性账号恢复码

恢复码由用户登录后主动设置，需要事先安全保存；系统没有短信或邮件找回服务。全部路径仍使用 `/api/v1` 前缀，写操作必须满足 CSRF 校验，所有恢复接口使用认证限流。

| 接口 | 请求 | 响应与约束 |
|---|---|---|
| `GET /auth/recovery` | 已登录 | 返回 `enabled,created_at`；不返回恢复码。 |
| `POST /auth/recovery` | 已登录，`{password}` 为当前密码 | 生成或替换恢复码，返回 `enabled,recovery_code,detail`；明码仅本次响应显示。 |
| `DELETE /auth/recovery` | 已登录，`{password}` 为当前密码 | 停用恢复码，返回 `enabled:false`。 |
| `POST /auth/recovery/reset` | `{username,recovery_code,new_password}`，无需登录 | 验证后重设密码、消耗恢复码并退出当前登录；不自动登录。 |

恢复码由 128 位安全随机值生成，按账号绑定，仅持久化 HMAC 摘要和密码版本标记；不会保存明码。使用、停用、重新生成或修改密码都会使原码失效；注销同样停用。重设拒绝弱密码和与原密码相同的新密码，验证失败不消耗有效码。已知用户名与错误码、未知账号使用一致的公开失败提示。成功响应使用 `Cache-Control: no-store, private`；客户端不要把恢复码写入 URL、浏览器持久存储或日志。

恢复与常规改密均锁定并重新读取账号，两个相同恢复码请求不能重复成功，恢复前已经认证的旧请求也不能用旧密码覆盖新密码。重设后其他设备的旧会话失效。若响应丢失，应先尝试用新密码登录；已经成功的恢复码不能再次使用。没有预先保存有效码的用户需要联系运营核验身份，系统无法读取旧密码或旧恢复码。

### 认证限流与可信代理边界

`AUTH_TRUST_PROXY_CLIENT_IP` 默认为 `false`：认证接口只用连接的 REMOTE_ADDR 作为限流标识，不采信客户端自行提供的 X-Forwarded-For 或 X-Real-IP。仅在后端不能被外部绕过、可信代理覆盖 X-Real-IP 为真实直连客户端地址时设置为 `true`；此时读取并校验 X-Real-IP，缺失或非有效 IP 时退回 REMOTE_ADDR，仍不使用 X-Forwarded-For。

根目录 `compose.yaml` 的后端无公开端口，`deploy/Caddyfile` 覆盖 X-Real-IP，因此该受控部署开启了此开关。若改成后端直接公开、增加代理层或允许不可信容器访问后端，必须重新配置边界，不能仅复制开关。它与用于 HTTPS 协议识别的 `TRUST_PROXY`、微信付款客户端 IP 的开关相互独立。后台与 API 登录失败已通过数据库原子计数跨进程共享；其他使用缓存的 API 限流仍需生产共享缓存或边缘限速，客户端 IP 开关不替代这些部署要求。

## 一人摊出餐与线上余量（2026-09-30）

迁移 `0011_counter_operations` 保留现有库存和订单，新增商品暂停供应、库存版本、盘点更正记录、本场接单截止、可选备餐单数上限、接单页同步时间及订单求助关联。容量和提前停单默认关闭；商家掉线只产生提示，不自动停止接单。

### 暂停供应、补货与盘点更正

商品的 stock 表示**尚未被订单预留的线上可售份数**，不是包含已接订单的现场总余量。商家应单独划分线上份数；现场售出后按实际未预留余量盘点，不能把正在制作或待付款订单的份数重新计入线上库存。

- `Product.sale_paused` 默认 false，可通过商品 PATCH 独立修改。暂停不清零库存、不取消已有订单；恢复只解除该标记，仍检查库存、上下架、摊位营业与交易权限。下单遇到暂停商品返回 409 `product_sale_paused`，附 product_id。
- `Product.stock_version` 默认 0，商品响应均返回。每次下单预留、取消/超时归还、增量补货、成功盘点更正都会递增；幂等重放和重复释放不再递增。编辑价格、口味、暂停供应等资料不变更库存版本。
- 商品 PATCH 含 stock 时整次拒绝，返回 400 `stock_edit_requires_correction`；新建商品仍可设置初始库存。`POST /merchant/stalls/:id/restock` 继续按新增份数补货，不接受绝对覆盖。
- `POST /merchant/products/:id/stock-correction` 接收 `{stock,expected_stock_version,idempotency_key,reason}`。stock 为 0–100000 的整数，版本为非负整数，key 长度 8–128，reason 为必填说明、不超过 200 字。成功返回 `{product,replayed}`。
- 更正在摊位、商品锁内先检查已成功的幂等记录，再检查版本。同 key、同内容返回当前最新商品且 `replayed:true`；同 key 不同内容返回 409 `idempotency_conflict`。版本不符返回 409 `stock_version_conflict` 并附最新 product，此时没有执行更正；客户端须让商家重新盘点确认，不能自动用新版本覆盖重试。
- 更正保存前后数量、版本、操作人和原因。Admin 商品页的“盘点线上余量”入口调用同一服务；已有商品 stock 只读，摊位页商品内联为只读链接。资料编辑不会用旧表单覆盖期间下单或补货后的余量。

### 备餐容量、本场截止与接单提示

- `PATCH /merchant/stalls/:id/profile` 可设置 `prep_capacity:null|1..100`；null 关闭，0 无效。容量按**订单数**计，`pending_payment + pending + preparing` 共用预留名额，单份和多份均占一单。出餐为 ready 或取消等终止状态释放名额；调小上限不会取消已有订单。
- 摊位响应增加 `prep_capacity`、`prep_active_orders`、`stop_orders_at`。新下单在原有摊位锁内检查容量，满额返回 409 `prep_capacity_reached`。在商品库存充足时仍可能因订单容量暂时不可下单。
- `POST /merchant/stalls/:id/status` 可传 `stop_orders_at`（带时区时间或 null），只对当前营业会话生效。到时仅停止新订单，返回 409 `ordering_stopped`；不会收摊、修改位置确认时间或自动处理旧单。允许过去时间表达立即停新单；有 closes_at 时不得晚于它。null 清除截止；新开营业会话不继承上一场截止。
- 页面单独保存截止时，必须发送 `{cutoff_only:true,expected_session_id,stop_orders_at}`，其中 expected_session_id 取摊位响应的 `business_session_id`（无会话时为 null，此时不可提交）。该分支拒绝混入 status、confirm_location 等字段；锁内只修改同一有效场次的截止，不会把另一设备的暂歇恢复为营业。场次变化/不存在返回 409 `business_session_changed`，同场次已收摊或到预计收摊时间返回 409 `business_session_ended`；客户端应刷新后让商家重新确认，不能自动重放到新场次。
- 已预留订单的付款确认跳过新增容量/截止条件及手动暂停接单，避免把订单自己占用的名额当作满额而退款。商品暂停也不撤回已经预留的订单。真正收摊、位置过期、商户资格等原有校验仍保留；任何成功原请求的同 key 重放仍返回原订单。
- `POST /merchant/stalls/:id/receiving-heartbeat` 只接受空对象 `{}`，需要所属商家权限，返回 `receiving_seen_at,receiving_status`，时间只由服务端生成且多设备不会倒退。前端应仅在订单成功同步、页面处于前台时约每 30 秒发送。
- 摊位响应中的 receiving_status 为 `unknown`（尚无同步）、`recent`（90 秒内）或 `stale`。`receiving_age_seconds` 由服务端计算并将负值归零，尚无同步为 null，心跳成功响应为 0；客户端应以此配合单调计时器更新提示，避免使用手机墙上时钟计算时间差。它只说明接单页最近成功同步，**不表示商家已阅读或接下某笔订单**，也不影响 can_order、营业状态或位置新鲜度。手机锁屏、切后台和断网后的通知可靠性仍未解决，不能据此宣称无人值守接单。

### 到摊查单与订单求助

- `POST /merchant/stalls/:id/pickup-lookup` 接收 `{pickup_code,number?}`。取餐码严格为 8 位数字，仅查询所属摊位状态为 ready 的自取订单；number 为可选完整订单号。单一结果返回商家订单对象，仍隐藏 pickup_code；查询不收款、不核销、不推进状态。
- 未匹配返回 404 `pickup_order_not_found`；多个待取单重码时返回 409 `pickup_code_ambiguous`，要求补充完整订单号，绝不返回候选订单列表。接口按账号限制 60 次/分钟，响应禁止缓存，审计仅记录匹配订单 ID 和摊位 ID，不记录输入取餐码。正式多进程部署仍需共享限流缓存及边缘限制。
- 订单响应新增 `merchant_contact_phone`，取当前经营主体联系电话；本人可读订单即使摊位暂时隐藏，也能取得该联系入口。没有电话时为空，不编造联系方式。
- `POST /feedback` 可在原 content/contact/idempotency_key 请求中增加 `order_id`（UUID），无需新增 kind。必须登录且订单属于本人；自动关联对应摊位，即使摊位隐藏也能提交。若另传 stall_id，必须与订单一致。联系方式仍不公开给其他用户；成功仅代表提交运营人工处理，不表示商家收到或已处理。

本轮新增行为测试位于 `market/test_counter.py`：22 项 SQLite 通过；隔离 PostgreSQL 的 43 项通过，包括新增 6 项容量/盘点竞争、已有库存/补货/口味/恢复码并发以及已有配送付款兼容验证。记录分别为 `test-results/counter-20260930-sqlite.txt`、`test-results/counter-20260930-postgres.txt`。验证未迁移、重置或写入运行中的演示数据库。

## 地图安全代理

同时配置 `AMAP_KEY` 和 `AMAP_SECURITY_CODE` 才向前端提供 Web key。前端设置 `_AMapSecurityConfig.serviceHost` 为 `/api/v1/amap-proxy/_AMapService`，再加载 JS API。服务器只向固定高德主机转发有限路径，拒绝非同源来源、任意 URL、重定向和超大响应，并注入安全码；安全码不会进入前端配置。没有配置时地图明确返回 503，列表正常可用。上线前须用真实高德 key 和白名单域名做验收。

## 验证

```powershell
.\.venv\Scripts\python.exe manage.py test market --verbosity 2
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py backup_demo
```

逐份口味和备餐行为测试位于 `market/test_tastes_preparation.py`，账号恢复测试位于 `market/test_recovery.py`、`market/test_recovery_concurrency.py`。本轮既有业务 SQLite 回归 112 项通过，记录为 `test-results/daily-business-regression-sqlite.txt`；口味/预估新 22 项及既有并发 8 项在 PostgreSQL 上通过，记录为 `test-results/daily-tastes-preparation-postgres-final.txt`。恢复码另有 2 项真实 PostgreSQL 请求竞争通过，记录为 `test-results/daily-recovery-concurrency-postgres-final.txt`；测试通过 pg_stat_activity 确认实际锁等待，覆盖同码竞争及旧会话改密竞争。SQLite 会明确跳过需要行锁的竞争测试。

早期接口验收曾通过 37 项 SQLite 测试和 4 项 PostgreSQL 竞争测试，覆盖账户、权限、CSRF、完整订单、价格/库存、位置快照、地图代理和商家操作。这些是历史阶段的数量，不代表当前全部测试数量。完整测试使用上方 `manage.py test market`，PostgreSQL 测试账号需具备创建隔离测试库的权限。复用 `tools/verify_operations.py` 时可指定 `--report <新报告路径>` 保留之前的报告。

前一轮用户端交付已在本机隔离的 PostgreSQL 17.11 + PostGIS 3.6.2 上执行当时全部 26 项测试并通过；迁移、geography 生成列/GiST 索引、真实并发锁均经过验证。还将包含一笔完成订单的临时数据库使用 `pg_dump` 导出并用 `pg_restore` 恢复到另一临时库，核对摊位/商品/订单数量、金额、库存、有效几何对象与外键约束一致。报告保存在 `test-results/postgres-verification.txt`，最终锁调整的四项复测保存在 `test-results/postgres-concurrency.txt`。这项演练没有使用或覆盖本地演示数据库。

Windows 可复用 `tools/verify_postgres.py` 做隔离验收：将 [EDB 官方 PostgreSQL 17 Windows 二进制 ZIP](https://productsdl.enterprisedb.com/download-postgresql-binaries) 保存为 `.runtime/postgresql.zip`，将 [PostGIS 官方 pg17 ZIP](https://download-cache.osgeo.org/postgis/windows/pg17/) 保存为 `.runtime/postgis.zip`，然后运行 `.venv\Scripts\python.exe tools/verify_postgres.py`。脚本只在新建 Temp 目录展开程序，使用随机数据库密码并绑定 `127.0.0.1:55432`；该端口已占用时直接拒绝启动，结束自动关闭自身数据库，不安装系统服务。仅复查竞争测试可附加参数 `market.tests.PostgreSQLConcurrencyTests`。

`backup_demo` 使用 SQLite 在线备份 API 生成备份，再通过独立只读连接验证完整性、外键及订单可读性。备份含个人数据，应受与数据库同等保护，不提交版本控制。

## 生产部署步骤与未验收条件

1. 使用独立 PostgreSQL 数据库并安装 PostGIS。由有权限的 DBA 预先 `CREATE EXTENSION postgis`，或给予首次迁移角色安装扩展的权限；运行时使用最小权限数据库角色。
2. 注入 production 环境、随机 SECRET_KEY、明确域名、HTTPS 来源、数据库 SSL 连接和高德配置。`DEMO_MODE=false`。缺少数据库、弱默认密钥或开启演示时启动会失败。
3. 运行迁移、`collectstatic --noinput`、`createsuperuser`。运行 `python manage.py check --deploy --fail-level WARNING`，并执行全部 PostgreSQL 测试。禁止导入演示数据库。
4. 使用 Waitress/Gunicorn 等 WSGI 进程（Windows：`.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 config.wsgi:application`），由 Nginx/Caddy 提供 HTTPS、前端 dist、静态文件和 `/api/`、`/admin/` 反向代理。仅可信代理覆盖 X-Forwarded-Proto 时设置 TRUST_PROXY=true。普通 JSON 请求建议限制 1 MB；图片上传接口需允许 multipart 至少 6 MB（应用限制图片 5 MB）。API/登录代理限速，保护数据库、媒体卷与备份目录。
5. 将 `manage.py expire_orders --loop` 作为独立自动重启服务。健康检查调用 `/api/v1/health`，采集应用错误、超时接单、过期位置与未取餐事件。正式运行建议用共享缓存支撑多进程限流；当前 DRF 限流缓存为进程内缓存，边缘代理必须限制滥用。
6. 每日使用 `pg_dump --format=custom --file=<受控备份路径> <数据库>`；备份到隔离的加密存储并规定保留期。恢复演练必须在隔离库使用 `pg_restore --exit-on-error --no-owner --dbname=<恢复库> <备份文件>`，核对订单、库存、外键与 PostGIS 扩展，再切换应用做只读检查。不得直接覆盖生产库。
7. 正式开启交易前录入学校与区域、真实商户资质和准入范围；位置变更重新核验；双设备验收下单至取餐及超时取消。

本机 PostgreSQL/PostGIS 并发与隔离数据库备份恢复已经验证。正式域名、HTTPS 证书及高德密钥尚未配置；目标生产环境的连接权限、持续备份/恢复、真实地图/定位和公网试运营仍需按以上步骤验收，本地运行不等于已上线。
