import { expect, test } from '@playwright/test'
import { installVoiceMock } from './voice-mock'

test('desktop voice discovery completes a real voucher purchase', async ({
  page,
}) => {
  await installVoiceMock(page)
  await page.goto('/')

  await expect(
    page.getByRole('heading', { name: /Find your own rhythm/i }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Search by voice' }).click()
  await expect(page.getByLabel('What would make this trip memorable?')).toHaveValue(
    'A relaxed family day with food and culture',
  )

  const assistant = page.getByRole('dialog', {
    name: 'Mai shopping assistant',
  })
  await expect(assistant).toBeVisible({ timeout: 120_000 })
  await expect(assistant.locator('.assistant-product').first()).toBeVisible()
  await expect(
    assistant.locator('.assistant-actions button[data-product-id]'),
  ).toHaveCount(0)
  await expect(
    assistant
      .locator('.assistant-product-actions')
      .first()
      .getByRole('button')
      .first(),
  ).toBeVisible()

  await page.evaluate(() => {
    ;(window as Window & { __voiceTranscript?: string }).__voiceTranscript =
      'What works if it rains?'
  })
  await assistant.getByRole('button', { name: 'Talk to Mai' }).click()
  await expect(assistant.locator('.message.user').last()).toContainText(
    'What works if it rains?',
  )
  await expect(assistant.locator('.assistant-thinking')).toHaveCount(0, {
    timeout: 120_000,
  })

  const listenButton = assistant
    .getByRole('button', { name: 'Read this response aloud' })
    .last()
  await listenButton.click()
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as Window & { __spokenText?: string }).__spokenText,
      ),
    )
    .toBeTruthy()

  await assistant.getByRole('button', { name: 'Close assistant' }).click()
  await page.locator('.product-grid .product-card').first().scrollIntoViewIfNeeded()
  await page.locator('.product-grid .product-card .add-button').first().click()

  const cart = page.getByRole('dialog', { name: 'Experience cart' })
  await expect(cart).toBeVisible({ timeout: 60_000 })
  await expect(cart.locator('.cart-item')).toHaveCount(1)
  await cart.getByRole('button', { name: 'Continue to checkout' }).click()

  const checkout = page.getByRole('dialog')
  await expect(
    checkout.getByRole('heading', { name: 'One last check' }),
  ).toBeVisible()
  // Typed rather than relying on a prefill: a real checkout must not arrive
  // filled in with somebody else's name, so the fields start empty.
  await checkout.getByTestId('checkout-name').fill('Alex Traveller')
  await checkout.getByTestId('checkout-email').fill('alex@example.com')
  await checkout
    .getByRole('button', { name: 'Confirm demo purchase' })
    .click()

  await expect(
    checkout.getByRole('heading', {
      name: 'Your Vietnam moments are booked',
    }),
  ).toBeVisible({ timeout: 60_000 })
  await expect(checkout.locator('.voucher-qr-image')).toBeVisible()
  await expect(checkout.locator('.voucher-details strong')).not.toBeEmpty()
})
