# 试用可靠性改进 · 2026-09-29

本轮集中修复模拟试用中会影响下单、设置保存与状态同步的问题。微信支付和配送继续使用明确标记的模拟模式，没有接通真实扣款或真实送餐。

## 已完成

- **原订单恢复**：下单响应丢失后，锁定编辑并显示原餐点、备注与金额；“确认原订单结果”重放原请求及原提交标识。刷新、离开返回、餐袋变动、摊位暂不可见及切换账号均保留相应隔离。恢复期间 CSRF 失败、鉴权失败、408/429 或无效响应不能清除原记录；明确的价格、库存等业务拒绝才解锁修改。
- **网络等待**：共享 API 默认约 20 秒超时，覆盖 CSRF、请求和响应内容读取；不自动重试写操作。一位调用者取消 CSRF 等待不影响另一位，也不会在离开后补发写请求。损坏的成功响应不再被当作成功保存。
- **订单同步提示**：失败后释放加载锁，显示最近同步时间或尚未同步；仍可重新加载。读取失败时不再显示“没有订单”，保留的旧记录明确标注为上次同步结果。
- **配送设置草稿**：只 PATCH 实际修改字段。未编辑项跟随最新服务设置，编辑项保留草稿，冲突提示另一设备的新值；保存期间冻结相关开关。切换摊位、账号后丢弃旧响应，基础设置与折叠的低频设置保持分组。
- **新摊位位置**：支持首次保存完整地址与经纬度。缺少字段返回可读的 400，失败原子回滚；首次录入或位置变化仍会撤销接单资格，等待重新核验。
- **模拟服务关闭**：接单资格暂停后仍可关闭模拟支付或配送；开启仍检查资格，所有权和模拟环境限制保留。
- **关注操作**：首页、地图、摊位详情和个人关注按摊位锁定提交，使用服务端结果，合并旧列表时保留已确认的关注状态。切换账号或离开页面取消等待中的请求。

## 验证记录

本轮浏览器套件拦截全部业务 API，没有向现有演练数据库写入测试订单。后端使用独立 PostgreSQL/PostGIS 集群验证，正式支付网络不在本轮验证范围。

| 范围 | 实际结果 |
| --- | --- |
| PostgreSQL：`market.test_merchant market.test_simulation market.tests` | 67 项通过，包括新增 7 项 API 回归及既有 7 项并发测试 |
| PostGIS、备份恢复 | 空间索引、完整订单、pg_dump/pg_restore 数据一致性通过，临时集群已停止 |
| `api-resilience.spec.ts` | 8 项通过，包含挂起请求、CSRF、损坏响应及订单重试 |
| `checkout-recovery.spec.ts` + `delivery-student.spec.ts` | 26 项通过（12 项恢复、14 项配送界面） |
| `merchant-delivery-drafts.spec.ts` + `delivery-merchant.spec.ts` + `merchant-simulation.spec.ts` | 20 项通过（6 项草稿、8 项配送、6 项模拟服务） |
| `simulation-student.spec.ts` + `payment-student.spec.ts` + `payment-merchant.spec.ts` | 20 项通过，覆盖模拟付款、支付占用、退款和模式标记 |
| `follow-reliability.spec.ts` | 8 项通过，覆盖双击、迟到列表、切换账号/摊位及 CSRF 等待期间离开 |

合计 82 项浏览器用例与 67 项后端测试通过，重复复验不重复计数。关注测试初次运行的路由匹配和离开时序断言已修正；最终整份 8 项套件通过。

商家套件首次运行有一项因并行清理 Playwright 输出造成 trace 文件 ENOENT，独立目录复验该项已通过；此后统一按套件分开临时输出。没有把失败结果略去或算作业务通过。

`npm run build`（Vue TypeScript 检查及 Vite 构建）通过。相关界面检查 360、390、768、1440 像素宽度，无横向溢出；代表截图已经人工查看并归档：

- [下单恢复 · 手机](previews/reliability-20260929/checkout-recovery-mobile.png)、[电脑](previews/reliability-20260929/checkout-recovery-desktop.png)
- [配送草稿 · 手机](previews/reliability-20260929/merchant-draft-mobile.png)、[电脑](previews/reliability-20260929/merchant-draft-desktop.png)
- [订单网络超时 · 手机](previews/reliability-20260929/orders-timeout-mobile.png)

后端详细日志：[merchant-reliability-20260929-postgres.txt](../backend/test-results/merchant-reliability-20260929-postgres.txt)。`checkout-isolation.spec.ts` 的按钮断言随新恢复入口更新，本轮没有运行这个会写现有演练数据库的套件，其身份隔离场景通过独立 `checkout-recovery` fixtures 覆盖。

## 本地访问与清理

已仅重启本项目后端以加载修复，继续使用前端 5183 / 后端 8087，模拟环境开启。2026-09-29 验证本机健康检查与同网络 `http://10.53.8.40:5183/merchant/store`、代理配置接口均返回成功；没有在实体手机上验证。IP 随网络变化，以启动脚本输出为准。

保留源码、演练数据库、商家上传、素材归属、备份、代表截图和本轮测试报告。清理尝试受到自动审批限制，工具仅返回 `blocked by policy`，没有强行删除或换途径绕过：

- `C:/Users/Microsoft/AppData/Local/Temp/yanhuo-postgres-check-wnb1b6kv`：已确认停库、无相关运行进程，约 518 MiB；递归删除被拦截，暂保留。
- 项目 `.runtime/merchant-draft-final-20260929`：关键截图已复制到 `docs/previews/reliability-20260929/`，递归删除同样被拦截。
- 因此本轮构建及测试临时输出（`frontend/dist`、`frontend/test-results`、相关 `.runtime` 测试目录）仍在磁盘，未宣称已清理完成。上一轮的清理记录不代表这些新输出已被删除。

## 后续试用事项

- 商家退款处理中、异常或待核对记录目前可从订单详情处理；独立的“退款待处理”计数与入口尚未加入。本轮没有把这个改进写成已实现。
- 后续更新：同日下一轮已加入商家“售后跟进”和学生“退款与待处理”。上条保留为本报告完成时的状态，新的行为、测试与截图见[售后跟进验证](followup-verification-20260929.md)，不计入本报告的测试总数。
- 地图正式配置、真实微信商户联调、HTTPS 部署和真实配送运营准备仍按原接入文档执行。模拟验证不能替代实际收款与送餐验证。
