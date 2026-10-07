import { test, expect, type Page } from '@playwright/test'
import { fulfillCsrf, assertNoHorizontalOverflow } from './helpers'

// Strict local fixtures: no request may reach a business server.
async function fixture(page: Page) {
  const product = { id: 713, name: '香菇饭', price_cents: 1000, stock: 2, stock_version: 1,
    is_active: true, sale_paused: true, category: '饭', image: '', taste_options: [], description: '' }
  const user = { id: 713, username: 'merchant-test', display_name: '测试商家', is_merchant: true, is_staff: false }
  const stall = { id: 713, name: '校园饭摊', products: [product], reviews: [], image: '',
    area_name: '校园南门', address: '位置尚未确认', latitude: 30, longitude: 120,
    status: 'open', session_status: 'open', prep_minutes: 10, accepting_orders: true,
    transaction_enabled: true, is_visible: false, can_order: true, contact_phone: '',
    activation: { has_location: false, steps: [
      { key: 'location', label: '真实位置', status: 'pending', owner: 'merchant', reason: '尚未提交真实位置' },
      { key: 'visibility', label: '公开展示', status: 'pending', owner: 'operator', reason: '等待运营核验' },
    ] }, services: { mode: 'live' } }
  const order: any = { id: '00000000-0000-4000-8000-000000000713', number: '713', stall_id: 713,
    stall_name: stall.name, status: 'preparing', mode: 'live', fulfillment_type: 'pickup',
    payment_method: 'offline', payment_status: 'unpaid', payment: null, refund: null, review: null,
    cancel_requested: false, payment_review_required: false, total_cents: 1000, pickup_code: '12345678',
    pickup_address: '校园南门', note: '', contact_phone: '',
    created_at: new Date().toISOString(), expires_at: new Date(Date.now()+240000).toISOString(),
    items: [{ product_id: 713, name: product.name, image: '', quantity: 1, unit_price_cents: 1000, portions: [] }] }
  const state = { order, stall, actions: [] as any[], restocks: [] as any[], unexpected: [] as string[],
    loseAction: true, loseRestock: true }
  await page.route('https://**/*', route => route.abort())
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/v1', '')
    const send = (json: any) => route.fulfill({ json })
    if (path === '/auth/csrf') return fulfillCsrf(route)
    if (path === '/config') return send({ user, demo_mode: false, areas: [], amap_key: '', stale_minutes: 60 })
    if (path === '/auth/me') return send(user)
    if (path === '/events') return send({})
    if (path === '/merchant/stalls') return send([stall])
    if (path === '/merchant/orders') return send({ results: order.status === 'completed' ? [] : [order],
      next: null, counts: { active: order.status === 'completed' ? 0 : 1, attention: 1 } })
    if (path === '/merchant/metrics') return send({ today: { revenue_cents: 0, orders_created: 1, orders_completed: 0 }, series: [], top_products: [], recent_payments: [] })
    if (path === `/merchant/orders/${order.id}/action`) {
      const body = req.postDataJSON(); state.actions.push(body)
      if (body.action === 'ready') order.status = 'ready'
      if (body.action === 'confirm_payment') order.payment_status = 'paid'
      if (body.action === 'complete') order.status = 'completed'
      if (state.loseAction) { state.loseAction = false; return route.abort('failed') }
      return send({ ...order })
    }
    if (path === '/merchant/stalls/713/restock') {
      const body = req.postDataJSON(); state.restocks.push(body)
      if (state.loseRestock) { state.loseRestock = false; return route.abort('failed') }
      product.stock += body.items[0].quantity
      return send({ ...stall })
    }
    state.unexpected.push(`${req.method()} ${path}`)
    return route.fulfill({ status: 500, json: { detail: 'Unexpected isolated request' } })
  })
  return state
}

test('merchant action retries preserve the original key after reload; payment and pickup stay separate', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/merchant/orders')
  await page.getByRole('group', { name: '订单阶段' }).getByRole('button', { name: /制作中/ }).click()
  await page.getByRole('button', { name: '做好了', exact: true }).click()
  // The saved intent is visible before CSRF and POST settle. Only reload after
  // the simulated server actually received the operation whose response is lost.
  await expect.poll(() => state.actions.length).toBe(1)
  await expect(page.getByRole('button', { name: /1 笔操作结果待确认/ })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: /1 笔操作结果待确认/ }).click()
  await page.getByRole('button', { name: '确认原操作结果', exact: true }).click()
  await expect.poll(() => state.actions.length).toBe(2)
  expect(state.actions[1]).toEqual(state.actions[0])
  expect(state.actions[0].idempotency_key.length).toBeGreaterThan(7)
  expect(state.order.payment_status).toBe('unpaid')
  await page.getByRole('group', { name: '订单阶段' }).getByRole('button', { name: /待取餐/ }).click()
  await page.getByRole('button', { name: '收款 ¥10', exact: true }).click()
  await page.getByRole('button', { name: '确认收款', exact: true }).click()
  await page.getByRole('button', { name: '核对取餐码', exact: true }).click()
  await expect(page.getByPlaceholder('输入取餐码', { exact: true })).toBeVisible()
  expect(state.order.status).toBe('ready')
  expect(state.order.payment_status).toBe('paid')
  await page.getByPlaceholder('输入取餐码', { exact: true }).fill('12345678')
  await page.getByRole('button', { name: '核销并完成', exact: true }).click()
  await expect.poll(() => state.order.status).toBe('completed')
  expect(state.actions.map(row => row.action)).toEqual(['ready', 'ready', 'confirm_payment', 'complete'])
  expect(new Set(state.actions.slice(1).map(row => row.idempotency_key)).size).toBe(3)
  expect(state.unexpected).toEqual([])
})

test('hidden stall activation explains ownership and never offers public sharing', async ({ page }, info) => {
  const state = await fixture(page)
  await page.goto('/merchant/more')
  await expect(page.getByText('公开展示尚未开通，暂不能分享学生端地址。工作台仍可继续完善资料。')).toBeVisible()
  await expect(page.getByRole('region', { name: '开摊准备' })).toContainText('尚未提交真实位置')
  await expect(page.getByRole('region', { name: '开摊准备' })).toContainText('由运营核验')
  await expect(page.locator('.stall-share')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '预览摊位', exact: false })).toHaveCount(0)
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await assertNoHorizontalOverflow(page)
  }
  await page.screenshot({ path: info.outputPath('activation-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: info.outputPath('activation-mobile.png'), fullPage: true })
  expect(state.unexpected).toEqual([])
})

test('quick restock keeps supply paused and reuses the whole batch after a network failure', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/merchant/products')
  await page.locator('.restock summary').click()
  await page.getByRole('button', { name: '香菇饭本批加5份', exact: true }).click()
  await page.getByRole('button', { name: '香菇饭本批加10份', exact: true }).click()
  await expect(page.getByLabel('香菇饭本次新增份数')).toHaveValue('15')
  await page.getByRole('button', { name: '确认本次补货', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试确认这批补货', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '重试确认这批补货', exact: true }).click()
  await expect.poll(() => state.restocks.length).toBe(2)
  expect(state.restocks[1]).toEqual(state.restocks[0])
  expect(state.restocks[0].items).toEqual([{ product_id: 713, quantity: 15 }])
  expect(state.stall.products[0]!.sale_paused).toBe(true)
  expect(state.unexpected).toEqual([])
})
