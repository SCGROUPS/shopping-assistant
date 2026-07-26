import type { CartItem } from '../types'
import { getResolvedLocale } from './api'
import { formatDate } from './format'

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
  label: string
  /** The assistant's opening line once the shopper accepts the offer. */
  opener: string
  /** Pre-filled message sent on behalf of the shopper, when one makes sense. */
  prompt?: string
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

const QUESTION_SHAPED =
  /^(what|which|where|when|why|how|who|can|could|would|should|is|are|do|does|did|help|find|show|suggest|recommend|plan|any|i need|i want|i'm|im|we need|we want|my|our)\b/i

const NARRATIVE_MARKER =
  /\b(with|without|and|but|for|near|around|plus|also|prefer|prefers|avoid|instead|because|while|during|who|that|under|over|before|after|between)\b/gi

/**
 * Decide whether a hero-search query should be answered by the assistant
 * instead of the grid.
 *
 * Deliberately conservative: short keyword queries such as "hoi an cooking
 * class" must always stay in the grid, because hijacking them would break the
 * shopper's expectation that the search box searches.
 */
export function isConversationalQuery(query: string): boolean {
  const trimmed = query.trim()
  if (!trimmed) return false
  const words = trimmed.split(/\s+/)
  if (words.length <= 3) return false
  if (trimmed.endsWith('?')) return true
  if (QUESTION_SHAPED.test(trimmed)) return true
  const markers = trimmed.match(NARRATIVE_MARKER)?.length ?? 0
  return words.length >= 6 && markers >= 2
}

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
    : 'the same time'

/**
 * Return at most one nudge, most specific first. Returning a single offer is
 * deliberate: stacked prompts read as a widget, not as an assistant.
 */
export function detectFriction(state: PresenceState): Nudge | null {
  if (state.checkoutOpen) {
    return {
      signal: 'checkout-hesitation',
      label: 'Questions before you book?',
      opener:
        'You are at checkout. Ask me anything about cancellation, meeting points, or what to bring before you confirm.',
    }
  }

  const clash = findScheduleClash(state.cartItems)
  if (clash) {
    const [first, second] = clash
    return {
      signal: 'schedule-clash',
      label: `These two clash at ${clockTime(first)} — want me to re-time one?`,
      opener: `“${first.experience.title}” and “${second.experience.title}” overlap at ${clockTime(first)}. I can move one to a later slot or another day.`,
      prompt: `${first.experience.title} and ${second.experience.title} overlap. Can you re-time one?`,
    }
  }

  if (state.hasSearched && state.resultCount === 0) {
    return {
      signal: 'zero-results',
      label: 'Nothing matched — want me to widen the dates?',
      opener:
        'Nothing was bookable with those constraints. I can widen the dates or drop the least important preference — your accessibility needs stay untouched.',
      prompt: 'Nothing matched. Can you widen my dates and try again?',
    }
  }

  if (state.viewedCount >= 3) {
    return {
      signal: 'comparison',
      label: 'Want me to compare these?',
      opener:
        'You have looked at a few of these. I can compare them on price, timing, and what the day actually feels like.',
      prompt: 'Compare the experiences I have been looking at.',
    }
  }

  if (state.refinementsSinceEngagement >= 3) {
    return {
      signal: 'refinement-loop',
      label: 'Narrowing this down? I can help.',
      opener:
        'You have refined this a few times. Tell me what the day should feel like and I will do the narrowing for you.',
    }
  }

  return null
}
