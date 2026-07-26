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
  await expect(page.getByTestId('locale-switcher')).toBeHidden()
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
  await expect(page.getByTestId('locale-switcher')).toBeHidden()
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

  await page.getByTestId('locale-switcher').selectOption('vi')
  // The switcher is disabled for the duration of the switch, so waiting for it
  // to come back is waiting for the refetch - no arbitrary sleep, which would
  // pass by finishing early on a fast run and flake on a slow one.
  await expect(page.getByTestId('locale-switcher')).toBeEnabled({ timeout: 60_000 })

  // Exercise the paths that build their own headers rather than relying on
  // `request()`: search, telemetry and a product view. A test that only loaded
  // the home page would prove nothing about the nine other call sites.
  // By test id, not by label: the label is itself translated, so querying it
  // in English after switching to Vietnamese looks for a string the feature
  // under test has just removed.
  const search = page.getByTestId('trip-search')
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
  await expect(page.getByTestId('locale-switcher')).toBeVisible()
  await page.getByTestId('locale-switcher').selectOption('vi')
  await expect(page.getByTestId('locale-switcher')).toHaveValue('en', {
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
  await expect(page.getByTestId('locale-switcher')).toBeVisible({ timeout: 60_000 })

  // Hold the catalogue refetch open only for the switch, so the window in
  // which chrome could run ahead of content is wide enough to observe. If the
  // locale were committed before the refetch, the page would be Vietnamese
  // around English cards for exactly this window - which reads as broken
  // rather than as loading.
  let release: (() => void) | null = null
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  // `experiences*` and not `experiences?**`: the latter needs a query string,
  // and a route that never matches makes this test pass by holding nothing.
  // The intercept count below is what turns that from a silent pass into a
  // failure.
  let intercepted = 0
  await page.route('**/api/v1/experiences*', async (route) => {
    intercepted += 1
    await held
    await route.continue()
  })

  await page.getByTestId('locale-switcher').selectOption('vi')
  await page.waitForTimeout(2_000)
  expect(intercepted).toBeGreaterThan(0)
  // A chrome string that appears exactly once, so this asserts the interface
  // language rather than whichever button happens to match first.
  await expect(page.getByText('Search the classic way')).toBeVisible()
  release?.()
})

test('a negotiated locale with no chrome falls back to English', async ({
  page,
}) => {
  // The backend negotiates from the browser's own Accept-Language, so it can
  // resolve a locale the interface cannot render. Hiding it from the switcher
  // is not enough: the session itself has to move, or the shopper gets Korean
  // descriptions in an English frame with no way back.
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'ko'], 'ko') }),
  )
  let switchedTo: string | undefined
  await page.route('**/api/v1/session/locale', async (route) => {
    switchedTo = JSON.parse(route.request().postData() ?? '{}').locale
    await route.fulfill({
      json: { locale: 'en', requested: 'en', enabled_locales: ['en', 'ko'] },
    })
  })

  await page.goto('/')
  // Polled rather than read once: the correction happens in the bootstrap's
  // async tail, so a single assertion after first paint races it and passes
  // for the wrong reason on a fast machine.
  await expect.poll(() => switchedTo, { timeout: 30_000 }).toBe('en')
})

test('a failed switch does not leave the preference ahead of the page', async ({
  page,
}) => {
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'vi']) }),
  )
  await page.goto('/')
  await expect(page.getByTestId('locale-switcher')).toBeVisible({ timeout: 60_000 })

  // The preference and the server session are changed before the refetches,
  // so a failure has to put them back. Otherwise the next request asks for a
  // language the screen is not showing.
  await page.route('**/api/v1/session/locale', (route) =>
    route.fulfill({ status: 500, json: { detail: 'nope' } }),
  )
  await page.getByTestId('locale-switcher').selectOption('vi')

  await expect(page.getByTestId('locale-switcher')).toBeEnabled({ timeout: 30_000 })
  await expect(page.getByTestId('locale-switcher')).toHaveValue('en')
  expect(await page.evaluate(() => localStorage.getItem('vietra-locale'))).not.toBe(
    'vi',
  )
})

test('the assistant speaks the shopper\'s language before the service replies', async ({
  page,
}) => {
  // The regression this exists for: the coverage gate reported Vietnamese
  // complete while every line the *client* composes - the greeting, the
  // nudges, the error replies - was still English, because it was written in
  // `.ts` modules and object literals rather than in markup. A dictionary the
  // gate can see is not the same as an interface the shopper can read.
  await page.route('**/api/v1/session/context', (route) =>
    route.fulfill({ json: bootstrap(['en', 'vi'], 'vi') }),
  )
  await page.goto('/')
  await page.getByTestId('assistant-open').click()

  const assistant = page.getByRole('dialog')
  await expect(assistant).toBeVisible()
  // Asserted on the Vietnamese text rather than on "not the English string":
  // an empty panel would satisfy the negative form.
  await expect(
    assistant.getByText('Xin chào! Chỉ với một vài sở thích'),
  ).toBeVisible()
  await expect(
    assistant.getByRole('button', { name: 'Xem lựa chọn cho gia đình' }),
  ).toBeVisible()
})
