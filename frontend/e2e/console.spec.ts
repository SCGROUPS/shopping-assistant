import { expect, test } from '@playwright/test'

// The console is only reachable with a key, so these run when one is
// supplied. In CI that is the demo bootstrap key; against a deployment it is
// a real operator key passed through the environment.
const KEY = process.env.ADMIN_API_KEY ?? 'demo-admin-key'

// The built-in key only exists in demo mode. Against a real deployment an
// explicit one is required, and skipping loudly beats failing a deploy over a
// missing secret.
test.skip(
  Boolean(process.env.PLAYWRIGHT_BASE_URL) && !process.env.ADMIN_API_KEY,
  'set ADMIN_API_KEY to exercise the console against a deployment',
)

test.describe('operator console', () => {
  test('refuses to show anything without a valid key', async ({ page }) => {
    await page.goto('/admin')
    await expect(page.getByRole('heading', { name: 'Vietra operations' })).toBeVisible()
    await page.getByLabel('Operator key').fill('definitely-not-a-key')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByText('That key is not recognised.')).toBeVisible()
    // No catalogue, no funnel, no audit: a rejected key must reveal nothing.
    await expect(page.locator('.ops-table')).toHaveCount(0)
  })

  test('an operator can review, edit, merchandise and configure', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })

    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('button', { name: 'Catalogue' })).toBeVisible()

    await page.getByRole('button', { name: 'Catalogue' }).click()
    await expect.poll(() => page.locator('.ops-table tbody tr').count()).toBeGreaterThan(5)
    await expect(page.getByPlaceholder('Search title, slug or destination')).toBeVisible()

    await page.locator('.ops-table tbody tr').first().click()
    await expect(page.locator('.ops-editor')).toBeVisible()

    const title = page.locator('.ops-editor input').first()
    await expect(title).toBeEnabled()
    await title.fill(`${await title.inputValue()} ·`)
    await page.getByRole('button', { name: 'Save details' }).click()
    // An edit must be recorded as an override, or the next import reverts it.
    await expect(page.locator('.ops-callout-quiet')).toBeVisible()

    // The row may already be pinned, so assert the toggle rather than a state.
    const alreadyPinned = await page.getByRole('button', { name: 'Unpin' }).count()
    const before = alreadyPinned ? 'Unpin' : 'Pin to top'
    const after = alreadyPinned ? 'Pin to top' : 'Unpin'
    await page.getByRole('button', { name: before }).click()
    await expect(page.getByRole('button', { name: after })).toBeVisible()

    await page.getByRole('button', { name: 'Configuration' }).click()
    await expect(page.getByRole('heading', { name: 'search_weights' })).toBeVisible()

    await page.getByRole('button', { name: 'Audit' }).click()
    // Every change just made has to be attributable to someone.
    await expect(page.getByText('catalog.update').first()).toBeVisible()

    await page.getByRole('button', { name: 'Performance' }).click()
    await expect(page.getByText('Assistant lift')).toBeVisible()

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })

  test('an override can be released back to the supplier feed', async ({ page }) => {
    // Without this an edit is a one-way door: the field is frozen against every
    // future supplier correction and the operator cannot undo it.
    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.getByRole('button', { name: 'Catalogue' }).click()
    await page.locator('.ops-table tbody tr').first().click()

    const meeting = page.getByLabel('Meeting point')
    await expect(meeting).toBeEnabled()
    // Unique per run: filling the value already there fires no change event, so
    // Save stays disabled and the test would depend on what ran before it.
    await meeting.fill(`Released-field browser check ${Date.now()}`)
    await page.getByRole('button', { name: 'Save details' }).click()

    const chip = page.locator('.ops-chip', { hasText: 'meeting_point' })
    await expect(chip).toBeVisible()
    await chip.getByRole('button', { name: 'release' }).click()
    await expect(chip).toHaveCount(0)
  })

  test('rejects a configuration change the server will not accept', async ({ page }) => {
    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.getByRole('button', { name: 'Configuration' }).click()

    const boost = page
      .locator('.ops-settings article')
      .filter({ hasText: 'max_merchandising_boost' })
    await boost.locator('textarea').fill('"not a number"')
    await boost.getByRole('button', { name: 'Save' }).click()
    // Ranking configuration is load-bearing: a bad value must never store.
    await expect(boost.locator('.ops-error')).toBeVisible()
  })
})

test.describe('translation review', () => {
  // §6.5 holds `meeting_point` back from the storefront because a
  // mistranslated set of directions sends a traveller to the wrong place. The
  // pipeline implemented the holding and nothing implemented the release, so
  // this screen is the difference between a review policy and a field that is
  // simply never published.
  //
  // The queue is served from a fixture rather than seeded, because what is
  // being tested here is the *contract the browser keeps*: that the decision
  // it sends names the version the reviewer actually read. The database
  // transitions are covered against real Postgres in
  // `backend/tests/test_translation_review.py`.
  const CANDIDATE = {
    experience_id: '11111111-1111-1111-1111-111111111111',
    title: 'Hoi An lantern walk',
    field: 'meeting_point',
    locale: 'vi',
    source_text: 'Japanese Bridge, old town',
    source_language: 'en',
    candidate_value: 'Cầu Nhật Bản, phố cổ',
    candidate_fingerprint: 'abc123',
    generation: 7,
    answers_current_source: true,
    status: 'needs_review',
    reviewed_by: '',
    updated_at: '2026-07-26T00:00:00Z',
  }

  test('a held translation can be read beside its source and approved', async ({ page }) => {
    let posted: Record<string, unknown> | null = null
    let decided = false

    await page.route('**/api/v1/admin/translations/review?*', async (route) => {
      await route.fulfill({
        json: decided
          ? { items: [], total: 0, by_locale: {} }
          : { items: [CANDIDATE], total: 1, by_locale: { vi: 1 } },
      })
    })
    await page.route('**/api/v1/admin/translations', async (route) => {
      await route.fulfill({
        json: {
          locales: [
            {
              locale: 'vi',
              enabled: false,
              percent: 74.8,
              needs_review: 379,
              fallback: 379,
              missing: 3,
              fields: {},
            },
          ],
        },
      })
    })
    await page.route('**/api/v1/admin/translations/review/approve', async (route) => {
      posted = route.request().postDataJSON()
      decided = true
      await route.fulfill({ json: { status: 'current', value: CANDIDATE.candidate_value } })
    })

    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.getByRole('button', { name: 'Languages' }).click()

    // The coverage table is the reason to open this screen: it says which
    // languages are live and how much of each is still showing English.
    await expect(page.getByText('74.8%')).toBeVisible()

    const candidate = page.locator('.ops-candidate').first()
    await expect(candidate).toBeVisible()
    // Both halves. Judging a translation from the proposal alone is not review.
    await expect(candidate.getByText('Japanese Bridge, old town')).toBeVisible()
    await expect(candidate.getByText('Cầu Nhật Bản, phố cổ')).toBeVisible()

    await candidate.getByRole('button', { name: 'Approve' }).click()
    await expect.poll(() => posted).not.toBeNull()

    // The version the reviewer read, not the version the server holds now.
    // Re-reading current state here would defeat the server's check entirely:
    // it would compare the row against itself and publish whatever it found.
    expect(posted).toMatchObject({
      experience_id: CANDIDATE.experience_id,
      field: 'meeting_point',
      locale: 'vi',
      candidate_fingerprint: 'abc123',
      generation: 7,
    })

    await expect(page.getByText('Nothing is waiting for a decision.')).toBeVisible()
  })

  test('a candidate whose source moved on is visible but cannot be approved', async ({ page }) => {
    // Hiding it would leave the field permanently unpublished with nothing to
    // notice; enabling Approve would publish a translation of text no shopper
    // will be shown. It has to be visible and refused.
    await page.route('**/api/v1/admin/translations/review?*', async (route) => {
      await route.fulfill({
        json: {
          items: [{ ...CANDIDATE, answers_current_source: false }],
          total: 1,
          by_locale: { vi: 1 },
        },
      })
    })
    await page.route('**/api/v1/admin/translations', async (route) => {
      await route.fulfill({ json: { locales: [] } })
    })

    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.getByRole('button', { name: 'Languages' }).click()

    const candidate = page.locator('.ops-candidate').first()
    await expect(candidate.getByText(/source text changed/i)).toBeVisible()
    await expect(candidate.getByRole('button', { name: 'Approve' })).toBeDisabled()
    // Both ways out stay open, or the field is stuck.
    await expect(candidate.getByRole('button', { name: 'Reject' })).toBeEnabled()
    await expect(candidate.getByRole('button', { name: 'Edit' })).toBeEnabled()
  })

  test('a rejected translation stays reachable so it can be written', async ({ page }) => {
    // Rejecting deliberately does not re-enqueue, because the same source and
    // the same recipe produce the same wrong address forever. A console that
    // lists only `needs_review` therefore strands every rejection on English
    // permanently - which is the defect this whole screen exists to fix, one
    // layer up. The first version of this feature had exactly that bug.
    let posted: Record<string, unknown> | null = null

    await page.route('**/api/v1/admin/translations/review?*', async (route) => {
      const rejected = new URL(route.request().url()).searchParams.get('state') === 'rejected'
      await route.fulfill({
        json: rejected
          ? {
              items: [
                {
                  ...CANDIDATE,
                  status: 'rejected',
                  reviewed_by: 'ops@vietra.test',
                  candidate_value: '',
                  candidate_fingerprint: '',
                  answers_current_source: false,
                },
              ],
              total: 1,
              by_locale: { vi: 1 },
            }
          : { items: [], total: 0, by_locale: {} },
      })
    })
    await page.route('**/api/v1/admin/translations', async (route) => {
      await route.fulfill({ json: { locales: [] } })
    })
    await page.route('**/api/v1/admin/translations/edit', async (route) => {
      posted = route.request().postDataJSON()
      await route.fulfill({ json: { status: 'current', provenance: 'manual' } })
    })

    await page.goto('/admin')
    await page.getByLabel('Operator key').fill(KEY)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.getByRole('button', { name: 'Languages' }).click()

    await expect(page.getByText('Nothing is waiting for a decision.')).toBeVisible()

    await page.locator('.ops-filters select').first().selectOption('rejected')

    const candidate = page.locator('.ops-candidate').first()
    await expect(candidate).toBeVisible()
    await expect(candidate.getByText(/Rejected by ops@vietra.test/)).toBeVisible()
    // The source must be here: it is what the reviewer translates from.
    await expect(candidate.getByText('Japanese Bridge, old town')).toBeVisible()
    // Nothing to approve, so approval is not offered at all.
    await expect(candidate.getByRole('button', { name: 'Approve' })).toHaveCount(0)

    await candidate.getByRole('button', { name: 'Write the translation' }).click()
    await candidate.locator('textarea').fill('Chân cầu Chùa Cầu')
    await candidate.getByRole('button', { name: 'Publish my version' }).click()

    await expect.poll(() => posted).not.toBeNull()
    // Pinned to the source version the reviewer read, like every other write.
    expect(posted).toMatchObject({
      experience_id: CANDIDATE.experience_id,
      locale: 'vi',
      value: 'Chân cầu Chùa Cầu',
      generation: 7,
    })
  })
})
