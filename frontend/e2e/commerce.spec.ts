import { expect, test } from '@playwright/test'

// Commercial surfaces — docs/SYSTEM_DESIGN.md §10.
// Two properties matter more than the pixels: a shopper always sees what they
// will actually be charged alongside the converted figure, and urgency badges
// are absent unless the inventory earns them.

async function searchFor(page: import('@playwright/test').Page, query: string) {
  const search = page.getByTestId('trip-search')
  await search.fill(query)
  await search.press('Enter')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })
}

test('changing display currency reprices the grid without hiding the charged amount', async ({
  page,
}) => {
  await page.goto('/')
  await searchFor(page, 'hoi an lantern')

  const price = page.locator('.product-grid .product-card .price').first()
  const inDong = await price.innerText()
  expect(inDong).toContain('₫')

  await page.getByLabel('Display currency').selectOption('USD')
  await expect(price).not.toHaveText(inDong, { timeout: 60_000 })

  const converted = await price.innerText()
  // The converted figure leads, but the authoritative VND amount stays on
  // screen: display currency is presentation, never the price we charge.
  expect(converted).toContain('$')
  expect(converted).toContain('₫')
})

test('urgency badges only appear on inventory that is genuinely tight', async ({
  page,
}) => {
  await page.goto('/')
  await searchFor(page, 'hoi an lantern')

  const cards = page.locator('.product-grid .product-card')
  const total = await cards.count()
  const flagged = await cards.locator('.signal-scarcity').count()
  expect(total).toBeGreaterThan(0)
  // Both bounds matter. Zero would mean the badge never fires and the check
  // passes vacuously; every card would mean the badge is decoration rather
  // than information.
  expect(flagged).toBeGreaterThan(0)
  expect(flagged).toBeLessThan(total)
})

test('a filled cart offers complements rather than a dead end', async ({
  page,
}) => {
  await page.goto('/')
  await searchFor(page, 'hoi an lantern')

  await page.locator('.product-grid .product-card').first().click()
  await page.getByRole('button', { name: /add to trip/i }).first().click()

  const drawer = page.locator('.cart-drawer')
  await expect(drawer).toBeVisible({ timeout: 60_000 })
  await expect(drawer.locator('.cart-cross-sell')).toBeVisible({
    timeout: 60_000,
  })
})
