import { expect, test } from '@playwright/test'

// Language switching — docs/CONTENT_PIPELINE.md §12 step 6.
//
// Production enables only `en` today, so the switcher is deliberately hidden
// there and no live journey can exercise it. These tests override the
// bootstrap to enable a second language, which is what makes them worth
// having: the header plumbing has to be correct *before* the coverage gate
// opens, not discovered to be wrong afterwards by shoppers.

const twoLocales = {
  session_id: 'locale-spec',
  assistant_enabled: true,
  assistant_holdout: false,
  locale: 'en',
  enabled_locales: ['en', 'vi'],
}

test('the switcher is hidden when only one language is enabled', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', async (route) => {
    await route.fulfill({
      json: { ...twoLocales, enabled_locales: ['en'] },
    })
  })
  await page.goto('/')
  await expect(page.getByLabel('Display currency')).toBeVisible()
  // A menu with one option is not a choice, and its presence would advertise
  // translations that do not exist.
  await expect(page.getByLabel('Language')).toBeHidden()
})

test('choosing a language sends it on every later request', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', async (route) => {
    await route.fulfill({ json: twoLocales })
  })
  await page.route('**/api/v1/session/locale', async (route) => {
    await route.fulfill({
      json: { locale: 'vi', requested: 'vi', enabled_locales: ['en', 'vi'] },
    })
  })

  // Recorded from the network rather than asserted per-call, because the bug
  // this guards against is one call site out of ten forgetting to forward the
  // preference - which any single-endpoint assertion would pass straight over.
  const seen: Array<{ path: string; language: string | undefined }> = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (!url.pathname.startsWith('/api/v1')) return
    seen.push({
      path: url.pathname,
      language: req.headers()['accept-language'],
    })
  })

  await page.goto('/')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })

  await page.getByLabel('Language').selectOption('vi')
  await page.waitForTimeout(3_000)

  const after = seen.slice(seen.findIndex((r) => r.path.endsWith('/session/locale')))
  expect(after.length).toBeGreaterThan(1)
  const missing = after.filter((r) => r.language !== 'vi')
  expect(
    missing,
    `these requests were made after the switch without Accept-Language: vi — ${missing
      .map((r) => `${r.path} (${r.language ?? 'absent'})`)
      .join(', ')}`,
  ).toEqual([])
})

test('the switcher shows what the server resolved, not what was asked', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', async (route) => {
    await route.fulfill({ json: twoLocales })
  })
  // The server negotiates. Asking for Vietnamese and being given English is a
  // normal outcome - the locale may be enabled while a particular field has no
  // current translation - and the menu must then read English, because that is
  // the language the page is actually in.
  await page.route('**/api/v1/session/locale', async (route) => {
    await route.fulfill({
      json: { locale: 'en', requested: 'vi', enabled_locales: ['en', 'vi'] },
    })
  })

  await page.goto('/')
  await expect(page.getByLabel('Language')).toBeVisible()
  await page.getByLabel('Language').selectOption('vi')
  await expect(page.getByLabel('Language')).toHaveValue('en', { timeout: 30_000 })
})
