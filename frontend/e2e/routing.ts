import type { Page } from '@playwright/test'

/**
 * Pin the routing decision a search comes back with.
 *
 * Where an answer belongs - the assistant or the grid - is the model's
 * judgement, and deliberately so: every rule that tried to decide it from the
 * text turned out to be deciding it from the language instead. That makes it
 * the wrong thing for a browser test to exercise. A test that let a real model
 * choose would be asserting the model's opinion, and would go red for reasons
 * that are not regressions; a test run without a model reachable would assert
 * nothing at all.
 *
 * So these tests fix the decision and check the storefront honours it, which
 * is the part of the behaviour the storefront actually owns.
 */
export const routeSearchAs = async (
  page: Page,
  mode: 'assistant' | 'grid' | 'undetermined',
): Promise<void> => {
  await page.route('**/api/v1/search', async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    await route.fulfill({ response, json: { ...body, interaction_mode: mode } })
  })
}
