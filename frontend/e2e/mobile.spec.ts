import { expect, test } from '@playwright/test'
import { installVoiceMock } from './voice-mock'

test('mobile search, assistant drawer, cart, and removal remain responsive', async ({
  page,
}) => {
  await installVoiceMock(page)
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
      (element) => element.getBoundingClientRect().width <= window.innerWidth,
    ),
  ).toBe(true)
  await assistant.getByRole('button', { name: 'Close assistant' }).click()

  const search = page.getByLabel('What would make this trip memorable?')
  await search.fill('Family day near Da Nang')
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
      (element) => element.getBoundingClientRect().width <= window.innerWidth,
    ),
  ).toBe(true)

  await cart.getByRole('button', { name: /^Remove / }).click()
  await expect(cart.getByText('Your adventure starts here')).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true)
})
