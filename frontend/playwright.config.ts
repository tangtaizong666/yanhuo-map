import { defineConfig, devices } from '@playwright/test'

// For real API flows use scripts/verify_browser_integration.py, which creates
// disposable servers/data and supplies E2E_ISOLATED + E2E_BASE_URL. Shared API
// helpers refuse writes and login on the working 5183/8087 preview. Intercepted
// UI fixtures can also run through playwright.ci.config.ts without a business DB.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5183',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 1000 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
