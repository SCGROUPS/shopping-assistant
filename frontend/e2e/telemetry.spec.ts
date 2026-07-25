import { expect, test } from '@playwright/test'

// Funnel instrumentation — docs/SYSTEM_DESIGN.md §13.
// Measurement that never reaches the backend is worse than none: it looks like
// evidence while being silence.

test('search, view and cohort telemetry reach the backend', async ({
  page,
  request,
}) => {
  const funnel = 'http://localhost:8000/api/v1/analytics/funnel'
  const before = await (await request.get(funnel)).json()

  await page.goto('/')
  const search = page.getByLabel('What would make this trip memorable?')
  await search.fill('hoi an cooking class')
  await search.press('Enter')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })
  await page.locator('.product-grid .product-card').first().click()
  await page.waitForTimeout(1_000)

  const after = await (await request.get(funnel)).json()
  expect(after.search_health.searches).toBeGreaterThan(
    before.search_health.searches,
  )
  expect(after.surfaces.grid?.experience_viewed ?? 0).toBeGreaterThan(
    before.surfaces.grid?.experience_viewed ?? 0,
  )
})
