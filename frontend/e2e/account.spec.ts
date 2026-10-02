import { expect, test } from '@playwright/test'
import { assertNoHorizontalOverflow, createStudent, login } from './helpers'

test('guest return destination, real login, and followed stalls survive reload', async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const student = await createStudent(context)
  await page.goto('/me')
  await expect(page).toHaveURL(/\/login\?returnTo=/)
  await login(page, student.username, student.password)
  await expect(page).toHaveURL(/\/me$/)
  await expect(page.getByRole('heading', { name: '自动化测试同学', exact: true })).toBeVisible()
  await page.goto('/')
  const firstStall = page.locator('.stall-card').first()
  const stallName = await firstStall.getByRole('heading').innerText()
  await firstStall.getByRole('button', { name: `关注${stallName}`, exact: true }).click()
  await expect(firstStall.getByRole('button', { name: `取消关注${stallName}`, exact: true })).toBeVisible()
  await page.goto('/me')
  await expect(page.getByRole('heading', { name: stallName, exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: stallName, exact: true })).toBeVisible()
  await assertNoHorizontalOverflow(page)
  await page.getByRole('button', { name: `取消关注${stallName}`, exact: true }).click()
  await expect(page.getByRole('heading', { name: '把喜欢的小摊，留在这里' })).toBeVisible()
})

test('registration form rejects mismatching passwords before submission', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByRole('textbox', { name: '账号', exact: true }).fill('e2e_mismatch_only')
  await page.getByRole('textbox', { name: '昵称', exact: true }).fill('检查密码同学')
  await page.getByLabel('密码', { exact: true }).fill('DifferentCampus!1')
  await page.getByLabel('确认密码', { exact: true }).fill('DifferentCampus!2')
  let registrationRequests = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/auth/register')) registrationRequests++ })
  await page.getByRole('button', { name: '创建账号' }).click()
  await expect(page.getByRole('alert')).toHaveText('两次输入的密码不一致，请重新确认。')
  expect(registrationRequests).toBe(0)
})
