# 配送验证记录

日期：2026-09-27。

本文件只记录本轮实际执行并得到结果的验证，不把计划、代码存在或测试中的模拟支付当作真实支付已开通。

这是基础配送接入阶段的记录，其中“默认未开放”的界面是当时配置。后续用户明确要求提供可操作的模拟体验，新增验证与当前操作见[模拟验证记录](simulation-verification.md)和[模拟体验指南](simulation-guide.md)；本文件的历史项数不计作新增模拟功能已通过。

## 当前边界

- 无真实微信商户号、HTTPS 域名与收款密钥，未进行真实扣款、退款或银行到账验证。
- 未联系或开通实际配送人员、目标校园交接点；未进行真人配送试运营。
- 自动化场景若使用替代网关或浏览器接口桩，会在结果中注明。正式环境仍需真实支付联调和双设备履约演练。

## 实际执行结果

| 范围 | 实际结果 | 条件与覆盖 |
| --- | --- | --- |
| 学生配送界面 `frontend/e2e/delivery-student.spec.ts` | 最终 14 项通过 | 独立浏览器 API 响应桩，不操作真实库存或支付；覆盖未开放原因、自取保留、交接点/联系方式/起送金额、幂等重试、运费变更确认、微信预付、剩余 75 秒创建边界、关闭付款、实际收餐、配送异常、退款说明和进行中订单 |
| 既有学生支付界面与入口 `payment-student.spec.ts`、`payment-visibility.spec.ts` | 16 项通过 | 使用隔离响应桩，检查配送改动后的自取付款、未知结果和支付入口行为 |
| 商家配送 `delivery-merchant.spec.ts` 与既有微信收退款 `payment-merchant.spec.ts` | 8 项配送与 5 项支付通过，共 13 项，9.9 秒 | 隔离浏览器接口响应桩；未付款配送禁用接单/线下收款，配送推进/收餐码、点位与费用、异常及解除、到账后待办、拒单退款说明、设置与拒绝反馈 |
| 学生端布局与截图 | 已检查 | 自动检查 360、390、768、1440 像素宽度无横向溢出；人工查看 390 像素配送结算与到点收餐截图 |
| 商家端布局与截图 | 已检查 | 360、390、768、1440 像素无横向溢出；人工查看 390 像素店铺设置/订单抽屉与 1440 像素桌面布局，截图是独立测试数据 |
| Vue/TypeScript/Vite 构建 | 通过 | 前端新增状态、组件和路由编译成功；构建不调用真实支付 |
| Django 全量业务测试，隔离 PostgreSQL 17 / PostGIS 3.6 | 141 项通过，124.529 秒 | 使用独立测试数据库和替代微信网关；含 17 项并发/事务测试（配送 7、支付 6、原订单 4），未以 SQLite 跳过并发代替验证 |
| 后端最终边界定向补测，同一 PostgreSQL/PostGIS 验证方式 | 3 项通过，3.199 秒 | 包含 2 项新测试和 1 项原测试追加断言：外部退款转人工核对、退款关闭后的注销保护、忽略旧退款状态仍更新查询时间；合并为 143 项不同的后端测试 |
| PostgreSQL 备份恢复演练 | 通过 | 隔离实例创建一笔已完成订单后执行 `pg_dump`/`pg_restore`；比对 8 个摊位、31 个商品、订单及明细、库存/金额、外键和 PostGIS GiST 索引；不是覆盖本地运行数据库 |
| 本地真实 API 浏览器回归 | 12 项通过，31.7 秒 | `checkout` 2 项验证自取下单/取消，`checkout-isolation` 3 项验证账户隔离，`merchant-entry` 6 项验证商家默认工作台与学生预览，`student-order-status` 1 项验证订单同步；仅开发示例环境，无真实收款 |
| 配送默认关闭的真实 API 检查 `delivery-live.spec.ts` | 2 项通过，3.7 秒 | 示例登录后读取商家配送设置、学生结算与未开放说明；不修改订单、设置或库存 |
| 本地迁移与同网络访问 | 通过 | 迁移 0005、0006 成功，Django 系统与迁移一致性检查通过；5183 继续监听 `0.0.0.0`，`http://10.53.8.40:5183/api/v1/health` 返回正常 |

最终学生配送 14 项与既有学生微信 8 项合并运行，22 项通过、12.9 秒；入口可见性 8 项在此前独立运行全部通过，商家 13 项全部通过。本轮合计验证 **43 项不同的界面测试**：新增配送 22 项（学生 14、商家 8），既有支付 21 项（学生 8、入口 8、商家 5），重复运行不重复计数。

学生配送最终结果当时输出到 `frontend/test-results-delivery-student-final/`；原始临时产物已在 2026-09-27 清理，运行状态汇总保存在[清理清单](cleanup-20260927.json)，代表截图继续保留在本文引用的 `docs/previews/`。此前一次 28 项组合运行有 27 项通过、1 项发现“订单处于退款状态但暂缺退款对象时，付款区隐藏”；已修复，并在最终配送测试中验证。开发服务跨天停止造成的首次连接拒绝属于测试环境未就绪，恢复前端服务后重跑，不作为功能通过或失败计数。

商家截图保存在 `docs/previews/merchant-delivery-settings-mobile.png`、`merchant-delivery-settings-desktop.png`、`merchant-delivery-detail-mobile.png`、`merchant-delivery-arrived-mobile.png`、`merchant-delivery-orders-desktop.png`。

已查看的代表截图：

- 真实本地页面，配送未开放：[学生手机结算](previews/live-student-delivery-mobile.png)、[商家手机配送设置](previews/live-merchant-delivery-mobile.png)。
- 独立测试数据，展示启用后的布局：[学生桌面结算](previews/student-delivery-checkout-desktop.png)、[学生桌面到点收餐](previews/student-delivery-arrived-desktop.png)、[商家手机订单](previews/merchant-delivery-detail-mobile.png)。这些响应桩截图不是实际商户已经开通或完成配送的证据。

全量后端输出见 `backend/test-results/delivery-full-postgres.txt`，最终定向补测输出见 `backend/test-results/delivery-boundaries-postgres.txt`。备份恢复报告包含 `BACKUP_RESTORE_MATCH`，PostGIS 版本 3.6、空间索引数量 1、无未验证外键；临时 PostgreSQL 实例在结束后关闭。`makemigrations --check` 与 Django `check` 通过，无遗漏迁移或系统检查问题。

审查中修复并补测了以下边界：付款入口创建不足一分钟被网关本地拒绝后不能无限占库存；支付查询期间商家停运须重新读取配置并退款；支付操作丢失临时占用后不能关闭后继在途请求；退款关闭通知不能被旧的处理中响应覆盖。支付和退款 HTTP 请求在数据库事务外执行，返回后重新锁定订单应用事实。

后端最终版本共验证 **143 项不同测试**，新增边界先在 SQLite 验证，再完成 PostgreSQL 定向复测；重复测试不重复计入总数。浏览器共验证 **57 项不同测试**：43 项隔离界面合约、12 项既有真实 API 回归、2 项配送默认状态真实 API 检查。

本地迁移前使用 SQLite 备份 API 保存 `.runtime/before-delivery-20260927-105002.sqlite3`，完整性检查正常。升级后的真实示例 API 返回 `delivery.enabled=false`、`approved=false`、`available=false`、`points=[]`，未伪造运营审核或开通配送。前端、后端与后台任务已恢复；局域网地址为当时电脑 IP，换网后需重新获取地址。HTTP 健康检查不等于已在真实手机上完成微信付款或配送。
