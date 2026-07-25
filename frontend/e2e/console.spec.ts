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
