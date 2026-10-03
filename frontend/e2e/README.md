# Browser acceptance tests

Start the seeded Django demo on port 8087 and Vite on port 5183, then run from `frontend`:

```powershell
npx playwright install chromium
npm run test:e2e
```

`E2E_BASE_URL` can override the frontend origin; add that origin to Django's `CSRF_TRUSTED_ORIGINS` as well. Mutating scenarios require `DEMO_MODE`, the demo `vendor / demo12345` account, an approved stall, and available product inventory. The suite creates uniquely named student accounts and real test orders without resetting the shared database. Completed orders consume one demo product; cancellation verifies inventory restoration.

Prefer a fresh, isolated database and media directory for all real API suites. The 2026-09-30 integration run used temporary 5193/8097 servers while the working preview stayed at 5183/8087. Never seed, reset, or broadly roll back the working demo to prepare tests. Stock fixture changes must use `correctStock()` from `helpers.ts`; ordinary product PATCH now rejects `stock`, even when other fields are included.

The `quick-choice`, `campus-student`, and `merchant-kitchen` suites fully intercept business APIs. They cover budget URL persistence, server-based activity age despite clock skew, paused food across cart/reorder/checkout, pickup card/contact recovery, kitchen sorting and lookup, capacity/cutoff controls, and inventory version conflict/unknown-result recovery. Keyboard checks emulate the `visualViewport` contract; real Android/iOS and WeChat browser behavior remains a separate checklist.

`counter-integration.spec.ts` requires `E2E_ISOLATED=1` in addition to `E2E_BASE_URL`, because it changes capacity and opening settings in its disposable fixture. It exercises real reservation/cancellation/restock/correction APIs, then looks up the resulting pickup order in the merchant browser and separately confirms payment and redemption. Do not enable this flag for the working demo database.

The suite covers four homepage viewport widths, direct links and search, account return paths, persisted follows, checkout and customer/merchant state changes in separate sessions, cash confirmation followed by pickup-code redemption, persisted reviews, pending cancellation, and price-only merchant edits preserving reserved inventory. API requests use the real session and CSRF cookie. The geolocation denial scenario injects only a failed third-party location provider; real map rendering and browser permission prompts still require configured AMap credentials.

Screenshots and failure traces are written to `test-results/`, with an HTML report in `playwright-report/`. Run against a stable server: editing Vue files during a test can reset a form through Vite hot reload. Each full Playwright run replaces previous test artifacts.

Keep representative screenshots in `docs/previews/` and verification notes in `docs/` before removing superseded run outputs. Prefer the default output directory instead of accumulating numbered or stage-specific `test-results-*` folders. The 2026-09-27 cleanup archived historical run statuses in `docs/cleanup-20260927.json`; application data, uploads, backups, test source, and backend text reports were retained.

For simultaneous **fully intercepted fixture suites**, give each runner a separate `--output=test-results/<suite>` directory; no runner may clean the parent output directory while another is using it. Archive useful screenshots and remove these temporary subdirectories after the run. Use `fulfillCsrf(route)` from `helpers.ts` for mocked CSRF responses: the client requires the same cookie contract as Django. Never run mutating suites concurrently against the shared demo database.

The reliability suites `api-resilience.spec.ts`, `checkout-recovery.spec.ts`, and `merchant-delivery-drafts.spec.ts` intercept all business API requests. They exercise hung/invalid responses, CSRF cancellation, recovery of the original uncertain checkout, and multi-device delivery drafts without writing the demo database. See `docs/reliability-verification-20260929.md` for this iteration's exact scope and results.

Additional workflow scenarios cover the multi-stall cart hub (separate checkout, undo, changed prices, stock, inactive/missing stalls and network recovery), reorder review against the live menu without creating an order, merchant reminders across pages and opt-in sound, and student ready-for-pickup reminders with stale-response/account-switch isolation. Run mutating suites serially (`workers: 1`); do not run separate copies against the same demo server at once. Test-specific products are deactivated afterwards, and each fixture cleans up its own orders. A deliberately completed test order remains a real historical test record.

## Pilot reliability fixtures (2026-10-03)

`pilot-reliability.spec.ts` intercepts all business APIs. It covers identity epochs, account/guest cart isolation and explicit merging, storage denial, product creation recovery with the same idempotency key, mutation/read races, cancel-dialog keyboard focus, cursor history pagination, all-page merchant attention, server financial holds, and terminal payment-entry suppression. Four viewport cases capture five key screens at 360/390/768/1440 px. These are Chromium simulations, not real-device or payment-gateway certification.

Cart fixtures now seed `yanhuo-cart-v2:user:<id>` for signed-in users or `yanhuo-cart-v2:guest` for anonymous users. The old unowned `yanhuo-cart-v1` is archived without automatically assigning it to an account. Account-switch checkout tests give each user their own draft explicitly.

Run the startup storage-denial case against a **production build** preview separately:

```powershell
npm run build
# In another terminal, start only this dedicated preview:
npm run preview -- --host 127.0.0.1 --port 5194 --strictPort
$env:E2E_BASE_URL = 'http://127.0.0.1:5194'
$env:E2E_PRODUCTION = '1'
npx playwright test pilot-reliability.spec.ts -g 'production startup'
```

This case makes both storage property getters throw before any app code loads. It passes for the production bundle. Vite development builds include a Pinia devtools dependency whose module initialization reads `localStorage` before application setup; the development fixture therefore checks denied app-key reads/writes and a denied getter after dependencies load. This development-only limitation is not represented as a production pass. The startup case skips unless `E2E_PRODUCTION=1` is explicitly set.
