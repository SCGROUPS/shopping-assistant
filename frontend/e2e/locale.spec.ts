import { expect, test } from '@playwright/test'

// Language switching — docs/CONTENT_PIPELINE.md §12 step 6.
//
// Production enables only `en` today, so no live journey can exercise this.
// These tests override the bootstrap, which is what makes them worth having:
// the plumbing has to be correct *before* the coverage gate opens, not
// discovered to be wrong afterwards by shoppers.

const bootstrap = (locales: string[], locale = 'en') => ({
  session_id: 'locale-spec',
  assistant_enabled: true,
  assistant_holdout: false,
  locale,
  enabled_locales: locales,
})

test('the switcher is hidden when only one language is enabled', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en']) }),
  )
  await page.goto('/')
  await expect(page.getByLabel('Display currency')).toBeVisible()
  // A menu with one option is not a choice, and its presence would advertise
  // translations that do not exist.
  await expect(page.getByLabel('Language')).toBeHidden()
})

test('a language the interface cannot render is not offered', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'ko']) }),
  )
  await page.goto('/')
  // Korean has catalogue coverage in this fixture but no chrome dictionary.
  // Offering it would produce a Korean catalogue inside an English interface,
  // which a shopper reads as broken rather than as partially translated.
  await expect(page.getByLabel('Language')).toBeHidden()
})

test('choosing a language sends it on every later request', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'vi']) }),
  )
  await page.route('**/api/v1/session/locale', (route) =>
    route.fulfill({
      json: { locale: 'vi', requested: 'vi', enabled_locales: ['en', 'vi'] },
    }),
  )

  // Recorded from the network rather than asserted per-call, because the bug
  // this guards against is one call site out of ten forgetting to forward the
  // preference - which any single-endpoint assertion would pass straight over.
  const seen: Array<{ path: string; language: string | undefined }> = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (!url.pathname.startsWith('/api/v1')) return
    seen.push({ path: url.pathname, language: req.headers()['accept-language'] })
  })

  await page.goto('/')
  const cards = page.locator('.product-grid .product-card')
  await expect(cards.first()).toBeVisible({ timeout: 60_000 })

  await page.getByLabel('Language').selectOption('vi')
  // The switcher is disabled for the duration of the switch, so waiting for it
  // to come back is waiting for the refetch - no arbitrary sleep, which would
  // pass by finishing early on a fast run and flake on a slow one.
  await expect(page.getByLabel('Language')).toBeEnabled({ timeout: 60_000 })

  // Exercise the paths that build their own headers rather than relying on
  // `request()`: search, telemetry and a product view. A test that only loaded
  // the home page would prove nothing about the nine other call sites.
  const search = page.getByLabel('What would make this trip memorable?')
  await search.fill('cooking class')
  await search.press('Enter')
  await expect(cards.first()).toBeVisible({ timeout: 60_000 })
  await cards.first().click()
  await page.waitForTimeout(1_000)

  const start = seen.findIndex((r) => r.path.endsWith('/session/locale'))
  const after = seen.slice(start + 1)
  expect(after.length).toBeGreaterThan(2)
  const missing = after.filter((r) => r.language !== 'vi')
  expect(
    missing,
    `requests made after the switch without Accept-Language: vi — ${missing
      .map((r) => `${r.path} (${r.language ?? 'absent'})`)
      .join(', ')}`,
  ).toEqual([])
})

test('the switcher shows what the server resolved, not what was asked', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'vi']) }),
  )
  // The server negotiates. Asking for Vietnamese and being given English is a
  // normal outcome, and the menu must then read English, because that is the
  // language the page is actually in.
  await page.route('**/api/v1/session/locale', (route) =>
    route.fulfill({
      json: { locale: 'en', requested: 'vi', enabled_locales: ['en', 'vi'] },
    }),
  )

  await page.goto('/')
  await expect(page.getByLabel('Language')).toBeVisible()
  await page.getByLabel('Language').selectOption('vi')
  await expect(page.getByLabel('Language')).toHaveValue('en', {
    timeout: 60_000,
  })
})

test('chrome does not switch ahead of the catalogue', async ({ page }) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'vi']) }),
  )
  await page.route('**/api/v1/session/locale', (route) =>
    route.fulfill({
      json: { locale: 'vi', requested: 'vi', enabled_locales: ['en', 'vi'] },
    }),
  )

  await page.goto('/')
  await expect(page.getByLabel('Language')).toBeVisible({ timeout: 60_000 })

  // Hold the catalogue refetch open only for the switch, so the window in
  // which chrome could run ahead of content is wide enough to observe. If the
  // locale were committed before the refetch, the page would be Vietnamese
  // around English cards for exactly this window - which reads as broken
  // rather than as loading.
  let release: (() => void) | null = null
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/v1/experiences?**', async (route) => {
    await held
    await route.continue()
  })

  await page.getByLabel('Language').selectOption('vi')
  await page.waitForTimeout(2_000)
  await expect(page.getByRole('button', { name: 'Ask Mai' })).toBeVisible()
  release?.()
})
