import type { CartItem } from '../types'
import { getResolvedLocale } from './api'
import { formatDate } from './format'
import { translate } from './i18n'
import type { LocalizedText } from './i18n'

/**
 * Assistant presence rules — see docs/SYSTEM_DESIGN.md §8.
 *
 * "Ambient, not interruptive. Earned, never automatic." Everything here fires
 * from observed friction; nothing fires on a timer, on scroll depth, or on page
 * load.
 */

export type FrictionSignal =
  | 'checkout-hesitation'
  | 'schedule-clash'
  | 'zero-results'
  | 'comparison'
  | 'refinement-loop'

export type Nudge = {
  signal: FrictionSignal
  /** One line shown on the launcher. Never opens the panel by itself. */
  label: LocalizedText
  /** The assistant's opening line once the shopper accepts the offer. */
  opener: LocalizedText
  /** Pre-filled message sent on behalf of the shopper, when one makes sense. */
  prompt?: LocalizedText
}

export type PresenceState = {
  hasSearched: boolean
  resultCount: number
  /** Searches run since the shopper last engaged with a result. */
  refinementsSinceEngagement: number
  /** Distinct products opened from the current result set. */
  viewedCount: number
  cartItems: CartItem[]
  checkoutOpen: boolean
}

// `isConversationalQuery` used to live here: a regex over English function
// words deciding whether a search became a conversation. It was removed rather
// than translated. Whichever language list it held, it would answer "not
// conversational" for every language not on it, so the guided path was
// unreachable for those shoppers no matter how clearly they asked. The server
// returns `interaction_mode` on the search response instead, because it is the
// only side that can read the query.

const overlaps = (item: CartItem, other: CartItem): boolean => {
  if (!item.starts_at || !other.starts_at) return false
  const start = new Date(item.starts_at).getTime()
  const otherStart = new Date(other.starts_at).getTime()
  const end = start + item.experience.duration_minutes * 60_000
  const otherEnd = otherStart + other.experience.duration_minutes * 60_000
  return start < otherEnd && otherStart < end
}

export function findScheduleClash(
  items: CartItem[],
): [CartItem, CartItem] | null {
  for (let index = 0; index < items.length; index += 1) {
    for (let other = index + 1; other < items.length; other += 1) {
      if (overlaps(items[index], items[other])) {
        return [items[index], items[other]]
      }
    }
  }
  return null
}

const clockTime = (item: CartItem) =>
  item.starts_at
    ? formatDate(getResolvedLocale(), item.starts_at, {
        hour: 'numeric',
        minute: '2-digit',
      })
    : translate(getResolvedLocale(), 'nudge.clash.sameTime')

/**
 * Return at most one nudge, most specific first. Returning a single offer is
 * deliberate: stacked prompts read as a widget, not as an assistant.
 */
export function detectFriction(state: PresenceState): Nudge | null {
  if (state.checkoutOpen) {
    return {
      signal: 'checkout-hesitation',
      label: { key: 'nudge.checkout.label' },
      opener: { key: 'nudge.checkout.opener' },
    }
  }

  const clash = findScheduleClash(state.cartItems)
  if (clash) {
    const [first, second] = clash
    return {
      signal: 'schedule-clash',
      label: { key: 'nudge.clash.label', vars: { time: clockTime(first) } },
      opener: {
        key: 'nudge.clash.opener',
        vars: {
          first: first.experience.title,
          second: second.experience.title,
          time: clockTime(first),
        },
      },
      prompt: {
        key: 'nudge.clash.prompt',
        vars: {
          first: first.experience.title,
          second: second.experience.title,
        },
      },
    }
  }

  if (state.hasSearched && state.resultCount === 0) {
    return {
      signal: 'zero-results',
      label: { key: 'nudge.zeroResults.label' },
      opener: { key: 'nudge.zeroResults.opener' },
      prompt: { key: 'nudge.zeroResults.prompt' },
    }
  }

  if (state.viewedCount >= 3) {
    return {
      signal: 'comparison',
      label: { key: 'nudge.comparison.label' },
      opener: { key: 'nudge.comparison.opener' },
      prompt: { key: 'nudge.comparison.prompt' },
    }
  }

  if (state.refinementsSinceEngagement >= 3) {
    return {
      signal: 'refinement-loop',
      label: { key: 'nudge.refinement.label' },
      opener: { key: 'nudge.refinement.opener' },
    }
  }

  return null
}
