import { expect, test, type Page } from '@playwright/test'
import { baseURL, fulfillCsrf } from './helpers'

// No real payment provider or business database is contacted by this suite.
const owner = { id: 881, username: 'cooldown_owner', display_name: '支付测试同学', is_merchant: false, is_staff: false }
const replacement = { ...owner, id: 882, username: 'cooldown_other', display_name: '另一位同学' }
function order(cooldown: number) {
  return {
    id: 'cooldown-fixture', number: 'COOLDOWN-ONLY', stall_id: 881,
    stall_name: '支付冷却测试摊位', status: 'ready', mode: 'live',
    fulfillment_type: 'pickup', payment_method: 'wechat', payment_status: 'unpaid',
    payment_review_required: false, payment_query_after_seconds: cooldown,
    allowed_actions: ['sync_payment', 'close_payment'],
    wechat_payment: { available: true, reason: '', channels: ['native'] },
    payment: { id: 'cooldown-attempt', status: 'pending', channel: 'native',
      code_url: '', h5_url: '', expires_at: '2026-10-03T07:00:00Z', next_query_at: null },
    refund: null, payment_can_close: true, total_cents: 1450,
    created_at: '2026-10-03T06:00:00Z', pickup_code: '123456',
    pickup_address: '测试取餐点', pickup_latitude: 45.7, pickup_longitude: 126.6,
    current_address: '测试取餐点', location_changed: false, note: '', contact_phone: '',
    cancel_requested: false, cancel_reason: '', review: null,
    items: [{ product_id: 881, name: '冷却测试餐点', image: '/images/food-cold-noodles.jpg', unit_price_cents: 1450, quantity: 1 }],
  }
}
async function fixture(page: Page, cooldown: number) {
  await page.clock.install({ time: new Date('2026-10-03T06:00:00Z') })
  await page.clock.pauseAt(new Date('2026-10-03T06:00:01Z'))
  await page.context().addCookies([{ name: 'csrftoken', value: 'fixture-csrf-token', url: baseURL }])
  let serverElapsedMs = 0
  let queryDeadline = cooldown * 1000
  const state = {
    user: owner, order: order(cooldown), calls: [] as string[], unexpected: [] as string[],
    beforeSync: null as null | (() => Promise<void>),
    beforeRead: null as null | (() => Promise<void>),
    async advanceClock(milliseconds: number) {
      serverElapsedMs += milliseconds
      await page.clock.runFor(milliseconds)
    },
  }
  // The simulated server advances only with elapsed test time, independently
  // of browser wall-clock corrections and ordinary order reads.
  function currentOrder() {
    return { ...state.order, payment_query_after_seconds: Math.max(0, Math.ceil((queryDeadline - serverElapsedMs) / 1000)) }
  }
  await page.route('https://**/*', route => route.abort())
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    const send = (json: unknown, status = 200) => route.fulfill({ status, json })
    if (path === '/config') return send({ demo_mode: true, brand: '烟火地图', amap_key: '', amap_proxy: '', areas: [], user: state.user })
    if (path === '/auth/me') return send(state.user)
    if (path === '/auth/csrf') return fulfillCsrf(route)
    if (path === '/orders/active-summary') return send([])
    if (path === `/orders/${state.order.id}`) {
      await state.beforeRead?.()
      return state.user.id === owner.id ? send(currentOrder()) : send({ detail: '当前账号无权查看此订单' }, 403)
    }
    const match = path.match(/\/payments\/(sync|close)$/)
    if (match) {
      state.calls.push(match[1])
      if (match[1] === 'sync') await state.beforeSync?.()
      if (match[1] === 'close') {
        state.order.payment.status = 'closed'
        state.order.payment_method = 'offline'
        state.order.payment_can_close = false
        state.order.allowed_actions = ['pay', 'cancel']
        queryDeadline = 0
      }
      return send(currentOrder())
    }
    state.unexpected.push(path)
    return send({ detail: `Unexpected fixture API: ${path}` }, 500)
  })
  await page.goto(`/orders/${state.order.id}`)
  await expect(page.getByRole('heading', { name: '支付方式', exact: true })).toBeVisible()
  return state
}

test('server cooldown disables manual sync and the monotonic countdown permits it after expiry', async ({ page }) => {
  const state = await fixture(page, 4)
  const refresh = page.locator('.student-payment .payment-actions button').first()
  await expect(refresh).toBeDisabled()
  await expect(refresh).toHaveText(/4 秒后可再次核对/)
  // DOM-dispatched activation also has to be rejected by the action guard.
  await refresh.dispatchEvent('click')
  let releaseRead!: () => void
  let readStarted!: () => void
  const readGate = new Promise<void>(resolve => { releaseRead = resolve })
  const reading = new Promise<void>(resolve => { readStarted = resolve })
  state.beforeRead = async () => { readStarted(); await readGate }
  const focusResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/orders/${state.order.id}`)
  const monotonicBefore = await page.evaluate(() => performance.now())
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await reading
  expect(state.calls).toEqual([])
  // A wall-clock correction must not bypass a server cooldown.
  await page.clock.setSystemTime(new Date('2026-10-04T06:00:01Z'))
  await expect(refresh).toBeDisabled()
  expect(await page.evaluate(() => performance.now())).toBe(monotonicBefore)
  // Release the ordinary focus GET after the correction, reproducing the
  // delayed response that previously read an eagerly cleared fixture value.
  state.order.stall_name = '焦点刷新后的支付测试摊位'
  releaseRead()
  const response = await focusResponse
  await response.finished()
  expect((await response.json()).payment_query_after_seconds).toBe(4)
  // A changed receipt proves the response was applied before advancing time.
  await expect(page.getByRole('heading', { name: state.order.stall_name, exact: true })).toBeVisible()
  await expect(refresh).toBeDisabled()
  await expect(refresh).toHaveText(/4 秒后可再次核对/)
  // Jump again after the response established the current deadline: a new
  // order response must not hide a regression to Date.now-based countdowns.
  await page.clock.setSystemTime(new Date('2026-10-05T06:00:01Z'))
  expect(await page.evaluate(() => performance.now())).toBe(monotonicBefore)
  await expect(refresh).toBeDisabled()
  await state.advanceClock(3000)
  await expect(refresh).toHaveText(/1 秒后可再次核对/)
  expect(state.calls).toEqual([])
  await state.advanceClock(1000)
  await expect(refresh).toHaveText('刷新付款状态')
  const syncResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/orders/${state.order.id}/payments/sync`)
  await refresh.click()
  await expect.poll(() => state.calls).toEqual(['sync'])
  const synced = await syncResponse
  await synced.finished()
  expect((await synced.json()).payment_query_after_seconds).toBe(0)
  expect(state.unexpected).toEqual([])
})

test('automatic payment polling respects the server cooldown then resumes', async ({ page }) => {
  const state = await fixture(page, 12)
  // At ten seconds an ordinary order read echoes the two remaining seconds.
  const orderResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/orders/${state.order.id}`)
  await state.advanceClock(10_000)
  const response = await orderResponse
  await response.finished()
  expect((await response.json()).payment_query_after_seconds).toBe(2)
  await expect(page.getByRole('button', { name: '2 秒后可再次核对' })).toBeDisabled()
  expect(state.calls).toEqual([])
  const syncResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/orders/${state.order.id}/payments/sync`)
  await state.advanceClock(10_000)
  await expect.poll(() => state.calls).toEqual(['sync'])
  const synced = await syncResponse
  await synced.finished()
  expect((await synced.json()).payment_query_after_seconds).toBe(0)
  expect(state.unexpected).toEqual([])
})

test('closing an existing payment remains available during query cooldown', async ({ page }) => {
  const state = await fixture(page, 60)
  await expect(page.getByRole('button', { name: '60 秒后可再次核对' })).toBeDisabled()
  const close = page.getByRole('button', { name: '关闭微信支付，改为到摊付款', exact: true })
  await expect(close).toBeEnabled()
  await close.click()
  await expect.poll(() => state.calls).toEqual(['close'])
  await expect(page.getByRole('button', { name: '微信支付', exact: true })).toBeEnabled()
  expect(state.unexpected).toEqual([])
})

test('a late payment response cannot restore the previous account order after identity changes', async ({ page }) => {
  const state = await fixture(page, 0)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  state.beforeSync = async () => {
    await gate
    state.order.payment_status = 'paid'
    state.order.payment.status = 'paid'
  }
  await page.getByRole('button', { name: '刷新付款状态', exact: true }).click()
  await expect.poll(() => state.calls).toEqual(['sync'])
  state.user = replacement
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByRole('alert')).toContainText('当前账号无权查看此订单')
  await expect(page.getByRole('heading', { name: '支付方式', exact: true })).toHaveCount(0)
  release()
  await expect.poll(() => state.order.payment_status).toBe('paid')
  await state.advanceClock(20_000)
  await expect(page.getByRole('alert')).toContainText('当前账号无权查看此订单')
  await expect(page.getByText('微信支付已确认', { exact: true })).toHaveCount(0)
  await expect(page.getByText('COOLDOWN-ONLY', { exact: true })).toHaveCount(0)
  expect(state.calls).toEqual(['sync'])
  expect(state.unexpected).toEqual([])
})
