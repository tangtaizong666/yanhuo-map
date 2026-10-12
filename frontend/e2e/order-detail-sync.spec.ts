import { expect, test, type Page } from '@playwright/test';
import QRCode from 'qrcode';

// All API traffic is fulfilled in this browser. No development orders are written.
function order(status = 'pending'): any {
  return {
    id: 'sync-order', number: 'SYNC-ORDER', stall_id: 987, stall_name: '状态验证小摊',
    mode: 'live', fulfillment_type: 'pickup', status, payment_status: status === 'completed' ? 'paid' : 'unpaid',
    payment_method: 'offline', payment_review_required: false, payment: null, refund: null,
    offline_payment_available: status === 'ready', stall_payment_qr_image: '/media/order-sync/collection-test.svg',
    wechat_payment: { available: false, reason: '未开通', channels: [] }, total_cents: 1600,
    created_at: new Date().toISOString(), pickup_code: '12345678', pickup_address: '南门取餐点',
    contact_phone: '', merchant_contact_phone: '13800138000', note: '', cancel_requested: false,
    cancel_reason: '', review: null, allowed_actions: ['pending', 'preparing', 'ready'].includes(status) ? ['cancel'] : [],
    items: [{ product_id: 987, name: '招牌烤冷面', image: '/images/food-cold-noodles.jpg', unit_price_cents: 1600, quantity: 1 }],
  };
}
async function fixture(page: Page, initial = order()) {
  const user = { id: 987, username: 'sync_student', display_name: '验证同学', is_merchant: false, is_staff: false };
  const state = { order: initial, user, reads: 0, writes: [] as string[], loseCancelResponse: false, holdMutation: false, cancelResponse: null as any, reviewResponse: null as any, failReads: false, rejectCancel: false, skipCancelMutation: false };
  let holdRead = false, releaseRead = () => {}, markReadHeld = () => {};
  let readHeld = Promise.resolve();
  let mutationRelease = () => {}, markMutationHeld = () => {};
  const mutationHeld = new Promise<void>(resolve => { markMutationHeld = resolve; });
  const mutationGate = new Promise<void>(resolve => { mutationRelease = resolve; });
  await page.route('https://**/*', route => route.abort());
  const code = await QRCode.toString('UI-TEST-ONLY-NO-PAYMENT', { type: 'svg' });
  await page.route('**/media/order-sync/collection-test.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: code }));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', '');
    const send = (body: any, status = 200) => route.fulfill({ status, json: body });
    if (path === '/config') return send({ user: state.user, demo_mode: true, areas: [], amap_key: '' });
    if (path === '/auth/me') return send(state.user);
    if (path === '/auth/csrf') {
      await page.evaluate(() => { document.cookie = 'csrftoken=fixture-sync; Path=/; SameSite=Lax'; });
      return send({ csrfToken: 'fixture-sync' });
    }
    if (path === '/orders/active-summary') return send({ user_id: state.user.id, counts: { total: 0 }, order: null });
    if (path === '/orders') return send([]);
    if (path === '/orders/sync-order' && request.method() === 'GET') {
      state.reads++;
      if (state.failReads) return route.abort('failed');
      const snapshot = structuredClone(state.order);
      if (holdRead) {
        holdRead = false;
        const gate = new Promise<void>(resolve => { releaseRead = resolve; });
        markReadHeld();
        await gate;
      }
      return send(snapshot);
    }
    if (path === '/orders/sync-order/cancel' && request.method() === 'POST') {
      state.writes.push(path);
      if (state.rejectCancel) return send({ detail: '取消操作未被接受。', code: 'cannot_cancel' }, 409);
      if (!state.skipCancelMutation) state.order = { ...state.order, status: 'cancelled', allowed_actions: [], offline_payment_available: false };
      const response = structuredClone(state.order);
      if (state.holdMutation) { markMutationHeld(); await mutationGate; }
      return state.loseCancelResponse ? route.abort('failed') : send(state.cancelResponse ?? response);
    }
    if (path === '/orders/sync-order/review' && request.method() === 'POST') {
      state.writes.push(path);
      state.order = { ...state.order, review: { ...request.postDataJSON(), created_at: new Date().toISOString() } };
      return send(state.reviewResponse ?? state.order, 201);
    }
    throw new Error(`Unexpected fixture request: ${request.method()} ${path}`);
  });
  return {
    state,
    async holdRefresh() {
      await expect(page.getByRole('button', { name: '刷新订单状态', exact: true })).toBeVisible();
      readHeld = new Promise<void>(resolve => { markReadHeld = resolve; });
      holdRead = true;
      await page.getByRole('button', { name: '刷新订单状态', exact: true }).click();
      await readHeld;
    },
    async releaseRead() {
      const delivered = page.waitForResponse(response => response.url().endsWith('/orders/sync-order'));
      releaseRead();
      await (await delivered).finished();
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    },
    mutationHeld, releaseMutation: () => mutationRelease(),
  };
}
async function cancel(page: Page) {
  await page.getByRole('button', { name: '取消订单', exact: true }).click();
  await page.getByRole('button', { name: '确认取消', exact: true }).click();
}

test('confirmed cancellation updates immediately and an older poll cannot reopen it', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const fixtureState = await fixture(page);
  await page.goto('/orders/sync-order');
  await fixtureState.holdRefresh();
  await cancel(page);
  await expect(page.locator('h1')).toHaveText('订单已取消', { timeout: 2000 });
  await expect(page.locator('.toast-stack')).toHaveText('订单已取消');
  await fixtureState.releaseRead();
  await expect(page.getByRole('button', { name: '取消订单', exact: true })).toHaveCount(0);
  await expect(page.locator('h1')).toHaveText('订单已取消');
  expect(fixtureState.state.writes).toEqual(['/orders/sync-order/cancel']);
  await page.screenshot({ path: info.outputPath('cancel-confirmed-390.png'), fullPage: true });
});

test('confirmed review closes the form despite an older manual refresh', async ({ page }) => {
  const fixtureState = await fixture(page, order('completed'));
  await page.goto('/orders/sync-order');
  await fixtureState.holdRefresh();
  await page.getByPlaceholder('说说口味、分量，或是让你记住这个小摊的瞬间…').fill('份量合适，味道很好');
  await page.getByRole('button', { name: '分享这份好味道' }).click();
  await expect(page.getByText('已评价，感谢你的分享')).toBeVisible({ timeout: 2000 });
  await fixtureState.releaseRead();
  await expect(page.getByRole('button', { name: '分享这份好味道' })).toHaveCount(0);
  await expect(page.getByText('份量合适，味道很好', { exact: true })).toBeVisible();
  expect(fixtureState.state.writes).toEqual(['/orders/sync-order/review']);
});

test('lost cancel response schedules a fresh read after an obsolete poll completes', async ({ page }) => {
  const fixtureState = await fixture(page);
  fixtureState.state.loseCancelResponse = true;
  await page.goto('/orders/sync-order');
  await fixtureState.holdRefresh();
  await cancel(page);
  await expect(page.locator('.toast-stack')).toContainText('暂时连接不上');
  await fixtureState.releaseRead();
  await expect(page.locator('h1')).toHaveText('订单已取消', { timeout: 2000 });
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(fixtureState.state.reads).toBe(3);
  expect(fixtureState.state.writes).toEqual(['/orders/sync-order/cancel']);
});

test('confirmed cancellation keeps unresolved refund information above normal fulfilment', async ({ page }) => {
  const fixtureState = await fixture(page);
  await page.goto('/orders/sync-order');
  await fixtureState.holdRefresh();
  // Model the server discovering captured funds while cancelling this order.
  fixtureState.state.order = { ...fixtureState.state.order, payment_status: 'refunding', payment_method: 'wechat', refund: { status: 'processing', amount_cents: 1600 } };
  await cancel(page);
  await expect(page.locator('h1')).toHaveText('退款处理中，请留意结果', { timeout: 2000 });
  await fixtureState.releaseRead();
  await expect(page.locator('.pickup-card')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '取消订单', exact: true })).toHaveCount(0);
});

test('a late action from the previous account does not overwrite the new account or show success', async ({ page }) => {
  const fixtureState = await fixture(page);
  fixtureState.state.holdMutation = true;
  await page.goto('/orders/sync-order');
  await cancel(page);
  await fixtureState.mutationHeld;
  fixtureState.state.user = { ...fixtureState.state.user, id: 988, display_name: '第二位同学' };
  fixtureState.state.order = { ...order(), stall_name: '第二位同学的小摊' };
  const identity = page.waitForResponse(response => response.url().endsWith('/auth/me'));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await identity;
  await expect(page.locator('.receipt-header h2')).toHaveText('第二位同学的小摊');
  fixtureState.releaseMutation();
  await expect(page.locator('h1')).toHaveText('订单已提交，等待商家接单');
  await expect(page.locator('.toast-stack')).toBeEmpty();
});

for (const action of ['cancel', 'review'] as const) {
  test(`${action} rejects an incomplete or foreign order response and confirms through a fresh read`, async ({ page }) => {
    const fixtureState = await fixture(page, order(action === 'review' ? 'completed' : 'pending'));
    if (action === 'cancel') fixtureState.state.cancelResponse = { ...order('cancelled'), id: 'another-order' };
    else fixtureState.state.reviewResponse = {};
    await page.goto('/orders/sync-order');
    await fixtureState.holdRefresh();
    if (action === 'cancel') await cancel(page);
    else {
      await page.getByPlaceholder('说说口味、分量，或是让你记住这个小摊的瞬间…').fill('正常评价内容');
      await page.getByRole('button', { name: '分享这份好味道' }).click();
    }
    await expect(page.getByRole('alert')).toContainText('订单返回信息不完整');
    await expect(page.locator('.toast.success')).toHaveCount(0);
    await fixtureState.releaseRead();
    if (action === 'cancel') await expect(page.locator('h1')).toHaveText('订单已取消', { timeout: 2000 });
    else await expect(page.getByText('已评价，感谢你的分享')).toBeVisible({ timeout: 2000 });
    expect(fixtureState.state.reads).toBe(3);
    expect(fixtureState.state.writes).toHaveLength(1);
  });
}

async function requestReadyCancellation(page: Page) {
  await page.getByRole('button', { name: '申请取消', exact: true }).click();
  await page.getByRole('button', { name: '提交申请', exact: true }).click();
}

test('collection code disappears while cancellation response is still pending', async ({ page }) => {
  const fixtureState = await fixture(page, order('ready'));
  fixtureState.state.holdMutation = true;
  await page.goto('/orders/sync-order');
  await expect(page.locator('.stall-qr img')).toBeVisible();
  await requestReadyCancellation(page);
  await fixtureState.mutationHeld;
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  await expect(page.locator('.pickup-card')).toHaveCount(0);
  await expect(page.locator('.student-payment')).toContainText('正在确认取消结果');
  fixtureState.releaseMutation();
  await expect(page.locator('h1')).toHaveText('订单已取消');
  await expect(page.locator('.stall-qr')).toHaveCount(0);
});

test('lost cancellation plus failed or old-state reads keeps collection hidden until cancellation is confirmed', async ({ page }) => {
  const fixtureState = await fixture(page, order('ready'));
  await page.goto('/orders/sync-order');
  await expect(page.locator('.stall-qr img')).toBeVisible();
  fixtureState.state.loseCancelResponse = true;
  fixtureState.state.skipCancelMutation = true;
  fixtureState.state.failReads = true;
  await requestReadyCancellation(page);
  await expect(page.locator('.toast-stack')).toContainText('暂时连接不上');
  await expect.poll(() => fixtureState.state.reads).toBeGreaterThan(1);
  await expect(page.locator('.order-detail-page > .error-message')).toBeVisible();
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  await expect(page.locator('.student-payment')).toContainText('正在确认取消结果');
  fixtureState.state.failReads = false;
  await page.reload();
  await expect.poll(() => fixtureState.state.reads).toBeGreaterThan(2);
  await expect(page.locator('.order-detail-page > .error-message')).toHaveCount(0);
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  await expect(page.locator('.student-payment')).toContainText('正在确认取消结果');
  fixtureState.state.order.payment = {
    id: 'other-device-payment', status: 'reconcile', mode: 'live', channel: 'native',
    code_url: '', h5_url: '', expires_at: new Date(Date.now() + 600000).toISOString(),
  };
  fixtureState.state.order.allowed_actions = ['sync_payment', 'close_payment'];
  await page.getByRole('button', { name: '确认取消结果', exact: true }).click();
  await expect(page.getByRole('button', { name: '刷新付款状态', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '关闭微信支付，改为到摊付款', exact: true })).toBeEnabled();
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  fixtureState.state.order.payment = null;
  fixtureState.state.order.allowed_actions = [];
  fixtureState.state.order.cancel_requested = true;
  fixtureState.state.order.offline_payment_available = false;
  await page.getByRole('button', { name: '刷新订单状态', exact: true }).click();
  await expect(page.locator('.student-payment')).toContainText('取消申请正在处理中');
  await expect(page.locator('.student-payment')).not.toContainText('正在确认取消结果');
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  expect(fixtureState.state.writes).toEqual(['/orders/sync-order/cancel']);
});

test('definitely rejected cancellation restores collection only after a successful state read', async ({ page }) => {
  const fixtureState = await fixture(page, order('ready'));
  await page.goto('/orders/sync-order');
  await expect(page.locator('.stall-qr img')).toBeVisible();
  fixtureState.state.rejectCancel = true;
  fixtureState.state.failReads = true;
  await requestReadyCancellation(page);
  await expect(page.locator('.toast-stack')).toContainText('取消操作未被接受');
  await expect.poll(() => fixtureState.state.reads).toBeGreaterThan(1);
  await expect(page.locator('.order-detail-page > .error-message')).toBeVisible();
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.locator('.stall-qr')).toHaveCount(0);
  fixtureState.state.failReads = false;
  await page.getByRole('button', { name: '刷新订单状态', exact: true }).click();
  await expect(page.locator('.stall-qr img')).toBeVisible();
  await expect(page.locator('.student-payment')).not.toContainText('正在确认取消结果');
  expect(fixtureState.state.writes).toEqual(['/orders/sync-order/cancel']);
});
