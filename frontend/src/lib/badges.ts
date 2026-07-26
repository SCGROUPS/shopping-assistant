import type { Experience } from '../types'
import type { Translator } from './useLocale'
import type { MessageKey } from './i18n'
import { bcp47 } from './format'

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
  // An unknown code is dropped rather than shown: printing it raw would put
  // `sold_out` on the page in front of a shopper. Dropping it is still a
  // contract failure - a newer catalogue is describing the product in terms
  // this build cannot render - so it is reported where it is normalised, not
  // silently discarded here on every re-render.
  return key ? t(key) : null
}

// Sold out leads. The catalogue appends the availability code last, and the
// card shows only the first two badges, so an experience with instant
// confirmation and family suitability pushed `sold_out` off the end - the one
// badge a shopper cannot afford to miss was the one guaranteed to be dropped.
const BADGE_ORDER = ['sold_out']

export const badgeLabels = (product: Experience, t: Translator): string[] =>
  [...product.badges]
    .sort(
      (a, b) =>
        (BADGE_ORDER.includes(b) ? 1 : 0) - (BADGE_ORDER.includes(a) ? 1 : 0),
    )
    .map((code) => badgeLabel(code, product, t))
    .filter((label): label is string => label !== null)

// Which constraints the search relaxed, in the shopper's language. An unknown
// code is dropped rather than printed: putting `indoor_outdoor` in front of a
// shopper is worse than saying one fewer thing. The disagreement is reported
// where the response is normalised, not silently here on every re-render.
const RELAXATION_KEYS: Record<string, MessageKey> = {
  max_duration: 'relaxed.max_duration',
  rating: 'relaxed.rating',
  instant_confirmation: 'relaxed.instant_confirmation',
  free_cancellation: 'relaxed.free_cancellation',
  category: 'relaxed.category',
  indoor_outdoor: 'relaxed.indoor_outdoor',
  language: 'relaxed.language',
  family_friendly: 'relaxed.family_friendly',
  dates: 'relaxed.dates',
  budget: 'relaxed.budget',
  destination: 'relaxed.destination',
}

export const relaxationLabels = (codes: string[], t: Translator): string[] =>
  codes
    .map((code) => RELAXATION_KEYS[code])
    .filter((key): key is MessageKey => key !== undefined)
    .map((key) => t(key))

// Joining with ", " is an English habit. Vietnamese, Japanese and German each
// punctuate a list differently, and the platform already knows how.
// A constraint the search understood and refused. The shopper is told which
// one, because the alternative - results that quietly ignore the single thing
// they were most specific about - is indistinguishable from a correct answer.
const UNRESOLVED_KEYS: Record<string, MessageKey> = {
  date_unverified: 'unresolved.date_unverified',
  date_implausible: 'unresolved.date_implausible',
}

export const unresolvedLabels = (codes: string[], t: Translator): string[] =>
  codes
    .map((code) => UNRESOLVED_KEYS[code])
    .filter((key): key is MessageKey => Boolean(key))
    .map((key) => t(key))

export const relaxationSentence = (
  codes: string[],
  locale: string,
  t: Translator,
): string =>
  new Intl.ListFormat(bcp47(locale), {
    style: 'long',
    type: 'conjunction',
  }).format(relaxationLabels(codes, t))
