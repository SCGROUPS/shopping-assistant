import { expect, test } from '@playwright/test'
import { routeSearchAs } from './routing'

// Assistant presence rules — docs/SYSTEM_DESIGN.md §8.
// "Ambient, not interruptive. Earned, never automatic."

test('the assistant never auto-opens on page load', async ({ page }) => {
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: /Find your own rhythm/i }),
  ).toBeVisible()
  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  await expect(assistant).toBeHidden()
  await expect(page.getByRole('button', { name: /Ask Mai/ }).last()).toBeVisible()
})

test('a keyword query stays in the grid, a conversational query hands off', async ({
  page,
}) => {
  await routeSearchAs(page, 'grid')
  await page.goto('/')
  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  const search = page.getByTestId('trip-search')

  await search.fill('hoi an cooking class')
  await search.press('Enter')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })
  await expect(assistant).toBeHidden()

  await routeSearchAs(page, 'assistant')
  await search.fill('What can we do with a toddler and a wheelchair in Hoi An?')
  await search.press('Enter')
  await expect(assistant).toBeVisible({ timeout: 120_000 })
})

test('an undetermined routing decision never opens the assistant by itself', async ({
  page,
}) => {
  // `undetermined` means the model could not be reached. Treating it as a
  // request for the assistant would put a dialog in front of every shopper
  // during an outage; treating it as `grid` would make the outage invisible.
  // It renders as a grid, and it is reported.
  await routeSearchAs(page, 'undetermined')
  await page.goto('/')
  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  await page.getByTestId('trip-search').fill('something calm for my parents')
  await page.getByTestId('trip-search').press('Enter')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })
  await expect(assistant).toBeHidden()
})

test('asking about a card carries the product as the subject', async ({
  page,
}) => {
  await page.goto('/')
  const firstCard = page.locator('.product-grid .product-card').first()
  await expect(firstCard).toBeVisible({ timeout: 60_000 })
  const title = (await firstCard.locator('h3').first().innerText()).trim()

  await firstCard.getByRole('button', { name: /^Ask Mai about / }).click()

  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  await expect(assistant).toBeVisible()
  await expect(assistant.getByText(title, { exact: false }).first()).toBeVisible(
    { timeout: 60_000 },
  )
})

test('the launcher offers to widen dates when nothing matches', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })

  await page.route('**/api/v1/search', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        query_id: '00000000-0000-0000-0000-000000000000',
        effective_filters: {},
        items: [],
        facets: {},
        relaxed_preferences: [],
      }),
    })
  })

  const search = page.getByTestId('trip-search')
  await search.fill('snowboarding')
  await search.press('Enter')

  const nudge = page.locator('.assistant-nudge')
  await expect(nudge).toContainText('Nothing matched', { timeout: 30_000 })

  // Guardrail: the nudge must never open the panel by itself, and it must
  // stay dismissed for the rest of the session.
  await expect(
    page.getByRole('dialog', { name: 'Mai shopping assistant' }),
  ).toBeHidden()
  await nudge.getByRole('button', { name: 'Dismiss suggestion' }).click()
  await expect(nudge).toBeHidden()

  await search.fill('kitesurfing on the moon')
  await search.press('Enter')
  await expect(page.locator('.assistant-nudge')).toBeHidden()
})

test('a zero-result search offers choices instead of quietly dropping constraints', async ({
  page,
}) => {
  // The search used to answer an empty page by discarding whichever constraint
  // its own table ranked cheapest, so a shopper who set a budget could be shown
  // something well over it and only find out from a banner. Nothing is given up
  // now until they say so, and this is where they say it.
  await page.goto('/')
  await expect(page.locator('.product-grid .product-card').first()).toBeVisible({
    timeout: 60_000,
  })

  const authorised: string[][] = []
  await page.route('**/api/v1/search', async (route) => {
    const body = route.request().postDataJSON() as { relax_order?: string[] }
    const order = body.relax_order ?? []
    authorised.push(order)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        query_id: '00000000-0000-0000-0000-000000000000',
        effective_filters: {},
        // Results appear only once the shopper has authorised something.
        items: order.includes('budget')
          ? [
              {
                id: '11111111-1111-1111-1111-111111111111',
                title: 'Sunset river cruise',
                destination: 'Hoi An',
                category: 'Cruise',
                price: 1_500_000,
                currency: 'VND',
                rating: 4.8,
                images: [],
              },
            ]
          : [],
        facets: {},
        relaxed_preferences: order.includes('budget') ? ['budget'] : [],
        relaxation_candidates: order.includes('budget')
          ? []
          : ['budget', 'dates'],
      }),
    })
  })

  const search = page.getByTestId('trip-search')
  await search.fill('a cruise under my budget')
  await search.press('Enter')

  const offer = page.locator('.relaxation-offer')
  await expect(offer).toBeVisible({ timeout: 30_000 })
  await expect(offer).toContainText('Which of these could you set aside?')
  await expect(page.locator('.product-grid .product-card')).toHaveCount(0)

  // The first request must have authorised nothing at all.
  expect(authorised[authorised.length - 1]).toEqual([])

  await offer.getByRole('button', { name: 'Ignore my budget limit' }).click()

  await expect(page.locator('.product-grid .product-card')).toHaveCount(1)
  await expect(page.locator('.relaxation-notice')).toContainText('budget')
  await expect(offer).toBeHidden()
  expect(authorised[authorised.length - 1]).toEqual(['budget'])
})
