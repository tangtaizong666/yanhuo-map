import { defineConfig, devices } from '@playwright/test'

// All fixture API requests are intercepted. Neither server proxies a business DB.
process.env.E2E_BASE_URL = 'http://127.0.0.1:5196'
process.env.E2E_PRODUCTION = '1'
const fixtures = [
  'api-resilience.spec.ts', 'checkout-recovery.spec.ts', 'identity-refresh.spec.ts',
  'merchant-delivery-drafts.spec.ts', 'pilot-reliability.spec.ts',
  'discovery-contract.spec.ts',
  'checkout-guidance.spec.ts',
  'student-discovery.spec.ts', 'quick-choice.spec.ts', 'follow-reliability.spec.ts',
  'merchant-hardening.spec.ts',
  'payment-cooldown.spec.ts',
  'merchant-simple-flow.spec.ts',
  'merchant-modes.spec.ts',
  'merchant-products-simple.spec.ts', 'reference-product-flow.spec.ts',
  'merchant-opening-flow.spec.ts', 'merchant-handoff-panel.spec.ts', 'order-detail-sync.spec.ts',
  'payment-student.spec.ts', 'simulation-student.spec.ts', 'payment-visibility.spec.ts',
]
export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1,
  timeout: 60_000, expect: { timeout: 15_000 },
  outputDir: process.env.E2E_OUTPUT_DIR || './test-results/ci',
  reporter: [['list'], ['html', { open: 'never', outputFolder: process.env.E2E_REPORT_DIR || 'playwright-report' }]],
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    { command: 'node node_modules/vite/bin/vite.js --config vite.ci.config.ts --host 127.0.0.1 --port 5196 --strictPort', url: 'http://127.0.0.1:5196', reuseExistingServer: false },
    { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5197 --strictPort', url: 'http://127.0.0.1:5197', reuseExistingServer: false },
  ],
  projects: [
    { name: 'fixture-chromium', testMatch: fixtures, grepInvert: /production startup/, use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5196' } },
    { name: 'smoke-webkit', testMatch: 'pilot-reliability.spec.ts', grep: /storage denial leaves|cancel dialog traps|terminal unpaid intents/, use: { ...devices['Desktop Safari'], baseURL: 'http://127.0.0.1:5196' } },
    { name: 'student-webkit', testMatch: ['discovery-contract.spec.ts', 'checkout-guidance.spec.ts', 'payment-cooldown.spec.ts', 'payment-visibility.spec.ts', 'payment-student.spec.ts', 'quick-choice.spec.ts', 'checkout-recovery.spec.ts', 'reference-product-flow.spec.ts', 'order-detail-sync.spec.ts', 'identity-refresh.spec.ts'], grep: /checkout-guidance\.spec\.ts|identity-refresh\.spec\.ts|order-detail-sync\.spec\.ts|reference-product-flow\.spec\.ts|discovery-contract\.spec\.ts|payment-cooldown\.spec\.ts|payment-visibility\.spec\.ts|payment-student\.spec\.ts|reorder skips|paused product|a lost result locks|confirmed .* preserves/, use: { ...devices['Desktop Safari'], baseURL: 'http://127.0.0.1:5196' } },
    { name: 'merchant-webkit', testMatch: ['merchant-hardening.spec.ts', 'merchant-simple-flow.spec.ts', 'merchant-products-simple.spec.ts', 'merchant-opening-flow.spec.ts', 'merchant-handoff-panel.spec.ts', 'merchant-modes.spec.ts'], use: { ...devices['Desktop Safari'], baseURL: 'http://127.0.0.1:5196' } },
    { name: 'production-chromium', testMatch: 'pilot-reliability.spec.ts', grep: /production startup/, use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5197' } },
    { name: 'production-webkit', testMatch: 'pilot-reliability.spec.ts', grep: /production startup/, use: { ...devices['Desktop Safari'], baseURL: 'http://127.0.0.1:5197' } },
  ],
})
