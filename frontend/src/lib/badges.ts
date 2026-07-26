import type { Experience } from '../types'
import type { Translator } from './useLocale'
import type { MessageKey } from './i18n'

// The catalogue sends badge *codes*. Rendering them here means the text a
// shopper reads is always produced from their own dictionary, so it cannot
// drift out of agreement with the fact it describes - which is what happened
// when the badge itself was the fact, written in English.
const BADGE_KEYS: Record<string, MessageKey> = {
  instant_confirmation: 'badge.instant_confirmation',
  family_friendly: 'badge.family_friendly',
  free_cancellation: 'badge.free_cancellation',
  available: 'badge.available',
  sold_out: 'badge.sold_out',
}

export const badgeLabel = (
  code: string,
  product: Experience,
  t: Translator,
): string | null => {
  if (code === 'free_cancellation' && product.free_cancellation_hours) {
    return t('badge.free_cancellation_hours', {
      hours: product.free_cancellation_hours,
    })
  }
  const key = BADGE_KEYS[code]
  // An unknown code is dropped rather than shown. Printing the raw code would
  // put `sold_out` on the page in front of a shopper, and printing nothing at
  // all is the honest response to a badge this build does not understand.
  return key ? t(key) : null
}

export const badgeLabels = (product: Experience, t: Translator): string[] =>
  product.badges
    .map((code) => badgeLabel(code, product, t))
    .filter((label): label is string => label !== null)
