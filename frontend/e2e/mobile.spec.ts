import { expect, test } from '@playwright/test'
import { routeSearchAs } from './routing'
import { installVoiceMock } from './voice-mock'

test('mobile search, assistant drawer, cart, and removal remain responsive', async ({
  page,
}) => {
  await installVoiceMock(page)
  await routeSearchAs(page, 'assistant')
  await page.goto('/')

  await expect(
    page.getByRole('heading', { name: /Find your own rhythm/i }),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Search by voice' })).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true)

  await page.getByRole('button', { name: 'Menu' }).click()
  await expect(
    page.getByRole('button', { name: 'Ask Mai', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Ask Mai', exact: true }).click()

  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  await expect(assistant).toBeVisible()
  expect(
    await assistant.evaluate(
      // A drawer mid-transition reports sub-pixel float noise, so allow 1px.
      (element) => element.getBoundingClientRect().width <= window.innerWidth + 1,
    ),
  ).toBe(true)
  await assistant.getByRole('button', { name: 'Close assistant' }).click()

  const search = page.getByTestId('trip-search')
  // Conversational queries hand off to the assistant (docs/SYSTEM_DESIGN.md
  // §8.2); short keyword queries deliberately stay in the grid.
  await search.fill('A relaxed family day near Da Nang with food and culture')
  await search.press('Enter')
  await expect(assistant).toBeVisible({ timeout: 120_000 })
  await assistant.getByRole('button', { name: 'Close assistant' }).click()

  const firstCard = page.locator('.product-grid .product-card').first()
  await firstCard.scrollIntoViewIfNeeded()
  await firstCard.locator('.add-button').click()

  const cart = page.getByRole('dialog', { name: 'Experience cart' })
  await expect(cart).toBeVisible({ timeout: 60_000 })
  await expect(cart.locator('.cart-item')).toHaveCount(1)
  expect(
    await cart.evaluate(
      (element) => element.getBoundingClientRect().width <= window.innerWidth + 1,
    ),
  ).toBe(true)

  await cart.getByRole('button', { name: /^Remove / }).click()
  await expect(cart.getByText('Your adventure starts here')).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true)
})
