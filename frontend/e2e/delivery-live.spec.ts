import { expect, test } from '@playwright/test';
import { baseURL, mutate, assertNoHorizontalOverflow } from './helpers';

// Real local demo API reads; only authentication mutates the isolated session.
// No delivery configuration, stock, order or payment changes are made here.
test('live merchant service switches reflect the server mode and existing configuration', async ({page, context}, info) => {
  const config = await (await context.request.get(`${baseURL}/api/v1/config`)).json();
  test.skip(!config.demo_mode, 'Only use the local demo environment');
  const login = await mutate(context, '/auth/login', { username:'vendor', password:'demo12345' });
  expect(login.ok()).toBeTruthy();
  await page.setViewportSize({ width:390, height:844 });
  await page.goto('/merchant/store');
  await page.getByLabel('选择管理的摊位').selectOption('1');
  const services = await (await context.request.get(`${baseURL}/api/v1/merchant/stalls/1/services`)).json();
  await expect(page.getByRole('switch', { name:'线上支付', exact:true })).toHaveAttribute('aria-checked', String(services.online_payment_enabled));
  await expect(page.getByRole('switch', { name:'外卖配送', exact:true })).toHaveAttribute('aria-checked', String(services.delivery_enabled));
  const settings = page.locator('.merchant-delivery-settings');
  if (services.delivery_enabled) {
    await expect(settings.getByRole('heading', { name:'外卖基础设置' })).toBeVisible();
    await expect(settings.getByLabel('配送费（元）')).toHaveValue(String(services.delivery.fee_cents / 100));
  }
  await assertNoHorizontalOverflow(page);
  await page.locator('.merchant-services').scrollIntoViewIfNeeded();
  await page.screenshot({ path:info.outputPath('live-merchant-delivery-mobile.png') });
});

test('live student checkout reflects actual delivery readiness and retains pickup without placing an order', async ({page, context}, info) => {
  const config = await (await context.request.get(`${baseURL}/api/v1/config`)).json();
  test.skip(!config.demo_mode, 'Only use the local demo environment');
  const login = await mutate(context, '/auth/login', { username:'student', password:'demo12345' });
  expect(login.ok()).toBeTruthy();
  const stall = await (await context.request.get(`${baseURL}/api/v1/stalls/1`)).json();
  await page.addInitScript(product => localStorage.setItem('yanhuo-cart-v1', JSON.stringify({1:[{ product, quantity:1 }]})), stall.products[0]);
  await page.setViewportSize({ width:390, height:844 });
  await page.goto('/checkout/1');
  await page.getByRole('button', { name:/商家配送/ }).click();
  await expect(page.getByRole('button', { name:'提交配送订单，去付款', exact:true })).toBeDisabled();
  if (!stall.delivery.available) await expect(page.locator('.delivery-unavailable')).toContainText(stall.delivery.reason);
  else {
    await expect(page.getByLabel('校园交接点')).toBeVisible();
    await expect(page.locator('.delivery-facts')).toContainText(String(stall.delivery.fee_cents / 100));
    if (stall.delivery.mode === 'simulation') await expect(page.locator('.simulation-notice')).toContainText('不会真实扣款或安排送货');
  }
  await assertNoHorizontalOverflow(page);
  await page.locator('.fulfillment-card').scrollIntoViewIfNeeded();
  await page.screenshot({ path:info.outputPath('live-student-delivery-mobile.png') });
  await page.getByRole('button', { name:/到摊自取 免配送费/ }).click();
  await expect(page.locator('.checkout-payment-methods')).toContainText('到摊付款');
});
