import { demoExperiences } from '../data/demo'
import { formatTime } from './format'
import { translate } from './i18n'
import type {
  LocalizedText,
  MessageKey,
  MessageVars,
  PluralBase,
} from './i18n'
import type {
  AssistantAction,
  ContentFieldMeta,
  AssistantContext,
  AssistantMessage,
  CartItem,
  Experience,
  SearchFilters,
  Voucher,
} from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const ALLOW_DEMO_FALLBACK =
  import.meta.env.VITE_ALLOW_DEMO_FALLBACK === 'true'

const SESSION_ID =
  localStorage.getItem('vietra-session-id') ?? crypto.randomUUID()
localStorage.setItem('vietra-session-id', SESSION_ID)

// One function builds every header set. The first attempt had two - a
// conditional `Accept-Language` in `request()` and an unconditional one here -
// and because explicit headers win over the spread, this one's empty string
// silently overwrote the browser's own language on search, events, SSE and
// every cart mutation. A shopper could have browsed in Vietnamese and searched
// in English. Two places deciding one header is the same bug as two lists of
// the same fields: it does not fail, it disagrees.
const baseHeaders = (): Record<string, string> => ({
  'X-Session-ID': SESSION_ID,
  // Omitted, never empty. An empty `Accept-Language` is a claim about
  // language; absence lets the server fall back to the browser's own header,
  // which is a better guess than ours.
  ...(preferredLocale ? { 'Accept-Language': preferredLocale } : {}),
})

const jsonHeaders = (): Record<string, string> => ({
  ...baseHeaders(),
  'Content-Type': 'application/json',
})

// Endonyms: a language menu is read by someone who cannot yet read the page,
// so "Tieng Viet" is findable where "Vietnamese" is not.
export const LOCALE_NAMES: Record<string, string> = {
  en: 'English',
  vi: 'Tiếng Việt',
  zh: '中文',
  ja: '日本語',
  ko: '한국어',
  fr: 'Français',
  de: 'Deutsch',
  es: 'Español',
}

export const SUPPORTED_CURRENCIES = [
  'VND',
  'USD',
  'EUR',
  'GBP',
  'AUD',
  'SGD',
  'KRW',
  'JPY',
] as const

// The shopper's language, sent on every request as `Accept-Language`. The
// server negotiates and decides - the header is a preference, not an
// instruction - and every response says which locale it actually resolved,
// which is what the UI reads rather than assuming it got what it asked for.
let preferredLocale = localStorage.getItem('vietra-locale') ?? ''

export const setPreferredLocale = (locale: string) => {
  preferredLocale = locale
  // Removed rather than stored blank: "no preference" is the absence of a
  // choice, and an empty string in storage is a stored choice that happens to
  // be empty. Rolling back a failed switch can land here.
  if (locale) localStorage.setItem('vietra-locale', locale)
  else localStorage.removeItem('vietra-locale')
}

export const getPreferredLocale = () => preferredLocale

// What the server said it actually served, which is what times and counts
// produced down here must be formatted in. `preferredLocale` is a request and
// can differ; formatting to a language the page is not in is the same mistake
// as the switcher showing an unconfirmed choice.
let resolvedLocale = 'en'

export const getResolvedLocale = () => resolvedLocale

// Display currency is presentation state, so it lives here rather than being
// threaded through every call signature. The authoritative VND price always
// travels alongside it, and nothing that charges money reads this.
let displayCurrency =
  localStorage.getItem('vietra-display-currency') ?? 'VND'

export const setDisplayCurrency = (currency: string) => {
  displayCurrency = currency
  localStorage.setItem('vietra-display-currency', currency)
}

export const getDisplayCurrency = () => displayCurrency

const withDisplayCurrency = (params: URLSearchParams) => {
  if (displayCurrency && displayCurrency !== 'VND') {
    params.set('display_currency', displayCurrency)
  }
  return params
}

class ApiRequestError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...baseHeaders(),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const problem = await response
      .json()
      .catch(() => null) as Record<string, unknown> | null
    throw new ApiRequestError(
      String(problem?.detail ?? problem?.title ?? `Request failed: ${response.status}`),
      response.status,
    )
  }
  return response.json() as Promise<T>
}

const allowDemoFallbackOrThrow = (error: unknown) => {
  if (ALLOW_DEMO_FALLBACK) return true
  throw error
}

// The service sends an action `type`; the words on the button are ours, so they
// belong to the dictionary rather than to this map.
const actionLabels: Record<string, MessageKey> = {
  ADD_TO_CART: 'assistant.action.addToCart',
  CHECK_AVAILABILITY: 'assistant.action.checkAvailability',
  PREPARE_CHECKOUT: 'assistant.action.prepareCheckout',
  CONFIRM_SIMULATED_CHECKOUT: 'assistant.action.confirmSimulated',
  VIEW_VOUCHER: 'assistant.action.viewVoucher',
}

/**
 * Prose the assistant service wrote, which is already in the shopper's language
 * because the request carried their locale. It has no dictionary key and must
 * not acquire one. When the service says nothing, the fallback is ours, so it
 * does have a key.
 */
const assistantProse = (value: unknown): LocalizedText =>
  typeof value === 'string' && value.trim()
    ? { raw: value }
    : { key: 'assistant.defaultReply' }

// Sentences the assistant service writes for itself rather than relaying from
// the model. Those cannot be in the shopper's language - the service has no
// dictionary - so it sends a code and the numbers that go in it, and the words
// are chosen here. Every one of these lines used to arrive as English prose,
// including the ones a shopper only ever sees when the model is unreachable.
const KNOWN_MESSAGE_CODES = new Set([
  'assistant.msg.searchResults',
  'assistant.msg.noResults',
  'assistant.msg.noResults.ask',
  'assistant.msg.compareNeedsTwo',
  'assistant.msg.comparison',
  'assistant.msg.noComplement',
  'assistant.msg.noComplement.ask',
  'assistant.msg.complements',
  'assistant.msg.added',
  'assistant.msg.checkoutTotal',
  'assistant.msg.cannotBookYet',
  'assistant.msg.booked',
])

/**
 * The assistant's line, preferring the code over the prose.
 *
 * A code the server sends and this build does not know is reported, not
 * printed: `assistant.msg.added` in a chat bubble is worse than the English
 * sentence it shipped alongside, so the fallback is the prose.
 */
// Codes whose sentence changes with a count, so the wording has to be chosen
// against the shopper's language rather than by adding an "s".
const PLURAL_MESSAGE_CODES = new Set([
  'assistant.msg.searchResults',
  'assistant.msg.checkoutTotal',
])

// The service sends an amount and a currency as two plain fields. Pairing them
// here means the sentence is rendered with the shopper's own separators and
// symbol placement instead of the server's `1,500,000 VND`.
const readMessageVars = (value: unknown): MessageVars => {
  const raw = (value ?? {}) as Record<string, unknown>
  const vars: MessageVars = {}
  for (const [name, item] of Object.entries(raw)) {
    if (name === 'currency') continue
    if (name === 'total' && typeof item === 'number') {
      vars.total = {
        amount: item,
        currency: String(raw.currency ?? 'VND'),
      }
      continue
    }
    if (typeof item === 'string' || typeof item === 'number') vars[name] = item
  }
  return vars
}

const assistantMessage = (
  code: unknown,
  rawVars: unknown,
  prose: unknown,
): LocalizedText => {
  if (typeof code === 'string' && code) {
    if (KNOWN_MESSAGE_CODES.has(code)) {
      const vars = readMessageVars(rawVars)
      if (PLURAL_MESSAGE_CODES.has(code)) {
        return {
          plural: code as PluralBase,
          count: Number(vars.count ?? 0),
          vars,
        }
      }
      return { key: code as MessageKey, vars }
    }
    reportContractViolation('message_code', code)
  }
  return assistantProse(prose)
}

const normalizeAssistantAction = (
  action: Record<string, unknown>,
): AssistantAction => ({
  type: String(action.type) as AssistantAction['type'],
  // A label the service sent is prose it wrote in the shopper's language;
  // anything we choose ourselves has to come from the dictionary.
  label: action.label
    ? { raw: String(action.label) }
    : { key: actionLabels[String(action.type)] ?? 'assistant.action.continue' },
  experience_id: action.experience_id
    ? String(action.experience_id)
    : undefined,
  option_id: action.option_id ? String(action.option_id) : undefined,
  slot_id: action.slot_id ? String(action.slot_id) : undefined,
})

// A field the server was supposed to send and did not, or sent in a shape this
// build does not understand. Recorded rather than swallowed: every silent
// coercion in this file has eventually turned into a defect nobody could see,
// because the coerced value was indistinguishable from a real answer.
const reportContractViolation = (field: string, value: unknown): void => {
  console.error(`[contract] ${field} was ${JSON.stringify(value)}`)
  api.track('api_contract_violation', { field, value: String(value) })
}

// A fact the catalogue is required to state. Coercing an absent field to
// `false` would mark every experience sold out, non-refundable and unsuitable
// for families the moment a backend stopped sending it - and the storefront
// would render that with complete confidence. The safe value is still used, so
// the page works, but the disagreement is reported.
const readFact = (item: Record<string, unknown>, field: string): boolean => {
  const value = item[field]
  if (typeof value === 'boolean') return value
  reportContractViolation(field, value)
  return false
}

// Codes this build knows how to render. An unrecognised one means the
// catalogue is describing the product in terms the storefront cannot show, so
// it is reported once here rather than silently vanishing at render time.
const KNOWN_BADGE_CODES = new Set([
  'instant_confirmation',
  'family_friendly',
  'free_cancellation',
  'available',
  'sold_out',
])

// The constraints the search gave up to find results. Codes, because this
// sentence is read by the shopper: assembling it as English prose on the server
// meant a Vietnamese storefront explained its own compromises in English.
const KNOWN_RELAXATION_CODES = new Set([
  'max_duration',
  'rating',
  'instant_confirmation',
  'free_cancellation',
  'category',
  'indoor_outdoor',
  'language',
  'family_friendly',
  'dates',
  'budget',
  'destination',
])

const readRelaxations = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  const codes = value.map(String)
  for (const code of codes) {
    if (!KNOWN_RELAXATION_CODES.has(code)) {
      reportContractViolation('relaxed_preferences', code)
    }
  }
  return codes
}

// Constraints the server understood but did not apply. Unlike a relaxation,
// which the search chose in order to find something, these were refused - so an
// unknown code is reported and kept rather than dropped: the shopper is better
// served by a vague warning than by no warning at all.
const KNOWN_UNRESOLVED_CODES = new Set([
  'date_unverified',
  'date_implausible',
])

const readUnresolved = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  const codes = value.map(String)
  for (const code of codes) {
    if (!KNOWN_UNRESOLVED_CODES.has(code) && !code.startsWith('field.')) {
      reportContractViolation('unresolved_constraints', code)
    }
  }
  return codes
}

const readBadges = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  const codes = value.map(String)
  for (const code of codes) {
    if (!KNOWN_BADGE_CODES.has(code)) reportContractViolation('badges', code)
  }
  return codes
}

// How the server said this answer should be shown. An unrecognised value is
// reported rather than coerced: the storefront can render a grid safely while
// still making it visible that routing was never decided, which is the whole
// difference between a stale deployment and a silent regression.
const readInteractionMode = (
  value: unknown,
): 'assistant' | 'grid' | 'undetermined' => {
  if (value === 'assistant' || value === 'grid' || value === 'undetermined') {
    return value
  }
  reportContractViolation('interaction_mode', value)
  return 'undetermined'
}

const normalizeContentMeta = (
  value: unknown,
): Record<string, ContentFieldMeta> | undefined => {
  if (!value || typeof value !== 'object') return undefined
  const entries = Object.entries(value as Record<string, unknown>)
    .map(([field, meta]) => {
      if (!meta || typeof meta !== 'object') return null
      const record = meta as Record<string, unknown>
      return [
        field,
        {
          locale: String(record.locale ?? ''),
          provenance: String(record.provenance ?? 'unknown'),
          stale: record.stale === true,
          fallback: record.fallback === true,
        },
      ] as const
    })
    .filter((entry): entry is NonNullable<typeof entry> => entry !== null)
  return entries.length ? Object.fromEntries(entries) : undefined
}

const normalizeExperience = (item: Record<string, unknown>): Experience => {
  const backendOptions = Array.isArray(item.options)
    ? (item.options as Array<Record<string, unknown>>)
    : []
  const options = backendOptions.map((option) => {
    const prices = Array.isArray(option.prices)
      ? (option.prices as Array<Record<string, unknown>>)
      : []
    const adultPrice =
      prices.find((price) => price.participant_type === 'adult') ?? prices[0]
    const slots = Array.isArray(option.slots)
      ? (option.slots as Array<Record<string, unknown>>)
      : []
    return {
      id: String(option.id),
      name: String(option.name ?? translate(getResolvedLocale(), 'product.option.standard')),
      price: Number(adultPrice?.amount ?? item.price ?? 0),
      currency: String(adultPrice?.currency ?? item.currency ?? 'VND'),
      validity_type: option.validity_type
        ? String(option.validity_type)
        : undefined,
      start_times: slots.map((slot) =>
        formatTime(resolvedLocale, String(slot.starts_at)),
      ),
      slots: slots.map((slot) => ({
        id: String(slot.id),
        starts_at: String(slot.starts_at),
        status: slot.status ? String(slot.status) : undefined,
        capacity_remaining:
          slot.capacity_remaining === undefined
            ? undefined
            : Number(slot.capacity_remaining),
      })),
    }
  })
  const badges = readBadges(item.badges)
  return {
    id: String(item.id ?? item.experience_id),
    slug: String(item.slug ?? item.id),
    title: String(item.title ?? translate(getResolvedLocale(), 'product.untitled')),
    destination: String(item.destination ?? item.location ?? 'Vietnam'),
    location: String(item.location ?? item.destination ?? 'Vietnam'),
    category: String(item.category ?? 'Experience'),
    short_description: String(item.short_description ?? ''),
    description: item.description ? String(item.description) : undefined,
    image_url: String(item.image_url ?? ''),
    rating: Number(item.rating ?? 0),
    review_count: Number(item.review_count ?? 0),
    price: Number(item.price ?? options[0]?.price ?? 0),
    currency: String(item.currency ?? options[0]?.currency ?? 'VND'),
    display_price:
      item.display_price != null ? Number(item.display_price) : undefined,
    display_currency: item.display_currency
      ? String(item.display_currency)
      : undefined,
    scarcity: item.scarcity ? String(item.scarcity) : undefined,
    social_proof: item.social_proof ? String(item.social_proof) : undefined,
    duration_minutes: Number(item.duration_minutes ?? 60),
    tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
    badges,
    reason: item.reason ? String(item.reason) : undefined,
    content_meta: normalizeContentMeta(item.content_meta),
    locale: item.locale ? String(item.locale) : undefined,
    // Read, never inferred. These four facts used to be recovered by matching
    // English words in the badge text, so a translated catalogue would have
    // reported every experience as unavailable, non-refundable and unsuitable
    // for families - silently, because the parse always "succeeded".
    available:
      item.available === undefined && item.availability !== undefined
        ? item.availability === 'AVAILABLE'
        : readFact(item, 'available'),
    instant_confirmation: readFact(item, 'instant_confirmation'),
    free_cancellation: Number(item.free_cancellation_hours ?? 0) > 0,
    free_cancellation_hours: Number(item.free_cancellation_hours ?? 0),
    family_friendly: readFact(item, 'family_friendly'),
    accessibility_features: Array.isArray(item.accessibility_features)
      ? item.accessibility_features.map(String)
      : [],
    options,
    actions: Array.isArray(item.actions)
      ? (item.actions as Array<Record<string, unknown>>).map(
          normalizeAssistantAction,
        )
      : [],
  }
}

const normalizeProducts = (payload: unknown): Experience[] => {
  if (Array.isArray(payload)) {
    return payload.map((item) =>
      normalizeExperience(item as Record<string, unknown>),
    )
  }
  if (!payload || typeof payload !== 'object') return []
  const data = payload as Record<string, unknown>
  const items = data.items ?? data.results ?? data.products
  return Array.isArray(items)
    ? items.map((item) =>
        normalizeExperience(item as Record<string, unknown>),
      )
    : []
}

const normalizeCart = async (
  payload: Record<string, unknown>,
  knownProducts: Experience[],
): Promise<CartItem[]> => {
  const backendItems = Array.isArray(payload.items)
    ? (payload.items as Array<Record<string, unknown>>)
    : []
  const productsById = new Map(
    [...demoExperiences, ...knownProducts].map((product) => [product.id, product]),
  )
  const missingIds = [
    ...new Set(
      backendItems
        .map((item) => String(item.experience_id))
        .filter((id) => !productsById.has(id)),
    ),
  ]
  await Promise.all(
    missingIds.map(async (id) => {
      const detail = await request<Record<string, unknown>>(`/experiences/${id}`)
      const product = normalizeExperience(detail)
      productsById.set(product.id, product)
    }),
  )

  return backendItems.map((item) => {
    const known = productsById.get(String(item.experience_id))
    if (!known) {
      throw new Error(`Cart experience ${String(item.experience_id)} is unavailable.`)
    }
    // The backend resolved this title in the requested locale; the cached
    // product was resolved in whatever locale was current when it was fetched.
    // Preferring the cache meant a Vietnamese cart listing English titles -
    // and only for the products the shopper happened to have already seen,
    // which is the hardest kind of bug to notice.
    const title = item.experience_title
      ? String(item.experience_title)
      : known.title
    const product = title === known.title ? known : { ...known, title }
    const startsAt = item.starts_at ? new Date(String(item.starts_at)) : null
    const participants = Array.isArray(item.participants)
      ? (item.participants as Array<Record<string, unknown>>)
      : []
    const countFor = (...types: string[]) =>
      participants
        .filter((participant) => types.includes(String(participant.type)))
        .reduce((sum, participant) => sum + Number(participant.count ?? 0), 0)
    return {
      id: String(item.id),
      experience: product,
      option_id: String(item.option_id),
      option_name: String(item.option_name ?? ''),
      slot_id: item.slot_id ? String(item.slot_id) : undefined,
      starts_at: startsAt?.toISOString(),
      date: startsAt?.toISOString().slice(0, 10) ?? '',
      time: startsAt ? formatTime(resolvedLocale, startsAt) : undefined,
      adults: countFor('adult', 'senior', 'student'),
      children: countFor('child', 'infant'),
      total: Number(item.quoted_total ?? 0),
    }
  })
}

const demoCartItem = (
  product: Experience,
  date: string,
  adults: number,
  children: number,
): CartItem => ({
  id: crypto.randomUUID(),
  experience: product,
  option_id: product.options?.[0]?.id,
  option_name: product.options?.[0]?.name,
  slot_id: product.options?.[0]?.slots?.[0]?.id,
  date,
  time: product.options?.[0]?.start_times?.[0],
  adults,
  children,
  total: product.price * (adults + children),
})

export const filterDemoProducts = (
  query: string,
  filters: SearchFilters = {},
) => {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean)
  return demoExperiences.filter((experience) => {
    const searchable = [
      experience.title,
      experience.destination,
      experience.location,
      experience.category,
      experience.short_description,
      ...experience.tags,
    ]
      .join(' ')
      .toLowerCase()
    const matchesQuery =
      tokens.length === 0 ||
      tokens.some((token) => searchable.includes(token)) ||
      (query.toLowerCase().includes('family') && experience.family_friendly) ||
      (query.toLowerCase().includes('accessible') &&
        Boolean(experience.accessibility_features?.length))
    return (
      matchesQuery &&
      (!filters.destination ||
        experience.destination.toLowerCase() ===
          filters.destination.toLowerCase()) &&
      (!filters.category ||
        filters.category === 'All' ||
        experience.category === filters.category) &&
      (!filters.max_total_price ||
        experience.price <= filters.max_total_price) &&
      (!filters.family_friendly || experience.family_friendly) &&
      (!filters.free_cancellation || experience.free_cancellation)
    )
  })
}

export const api = {
  demoFallbackEnabled: ALLOW_DEMO_FALLBACK,

  /**
   * Fire-and-forget funnel telemetry.
   *
   * Never awaited and never allowed to throw: measurement must not be able to
   * break the thing it measures.
   */
  track(
    eventType: string,
    properties: Record<string, unknown> = {},
    options: { experienceId?: string; placement?: string } = {},
  ): void {
    void request('/events', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({
        event_type: eventType,
        experience_id: options.experienceId ?? null,
        placement: options.placement ?? null,
        properties,
      }),
    }).catch(() => undefined)
  },

  /** Cohort assignment, resolved before first paint. */
  async sessionContext(): Promise<{
    assistantEnabled: boolean
    locale: string
    enabledLocales: string[]
  }> {
    try {
      const payload = await request<Record<string, unknown>>('/session/context')
      return {
        assistantEnabled: payload.assistant_enabled !== false,
        // The locale the server *resolved*, which is not always the one asked
        // for: a language that is enabled in the browser but not yet in the
        // catalogue resolves to English, and a switcher showing the request
        // rather than the result would claim a translation nobody has.
        locale: (resolvedLocale = String(payload.locale ?? 'en')),
        enabledLocales: Array.isArray(payload.enabled_locales)
          ? payload.enabled_locales.map(String)
          : ['en'],
      }
    } catch {
      // A telemetry outage must not remove the assistant, and must not offer a
      // language list the server never confirmed.
      return { assistantEnabled: true, locale: 'en', enabledLocales: ['en'] }
    }
  },

  async setLocale(locale: string): Promise<string> {
    // Persisted server-side as well as locally, because the session preference
    // is what the assistant and any later device read - localStorage is this
    // browser's opinion, and the conversation outlives the tab.
    //
    // Failures propagate. This used to `catch { return locale }`, which
    // reported the language the caller asked for as the language the server
    // agreed to: the UI then switched, the session did not, and every later
    // request disagreed with the screen. A write whose whole purpose is the
    // server's answer must not invent one.
    const payload = await request<Record<string, unknown>>('/session/locale', {
      method: 'PUT',
      headers: jsonHeaders(),
      body: JSON.stringify({ locale }),
    })
    // `?? locale` here would have left the same hole the comment above
    // describes, one layer further in: a 200 that omits the field would report
    // the requested language as the agreed one. A response that does not name
    // a locale has not confirmed anything.
    const confirmed = payload.locale
    if (typeof confirmed !== 'string' || !confirmed) {
      throw new ApiRequestError('Locale change was not confirmed', 502)
    }
    return (resolvedLocale = confirmed)
  },

  async listExperiences(): Promise<Experience[]> {
    try {
      const params = withDisplayCurrency(new URLSearchParams())
      const query = params.toString() ? `?${params}` : ''
      return normalizeProducts(await request(`/experiences${query}`))
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return demoExperiences
    }
  },

  async search(
    query: string,
    filters: SearchFilters,
    partySize: number,
  ): Promise<{
    items: Experience[]
    intent?: Record<string, unknown>
    effectiveFilters: SearchFilters
    relaxedPreferences: string[]
    unresolvedConstraints: string[]
    facets: Record<string, Record<string, number>>
    interactionMode: 'assistant' | 'grid' | 'undetermined'
  }> {
    try {
      const payload = await request<Record<string, unknown>>('/search', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({
          query,
          filters,
          party: [{ type: 'adult', count: partySize }],
          sort: 'recommended',
          page_size: 24,
          display_currency: displayCurrency,
        }),
      })
      return {
        items: normalizeProducts(payload),
        intent: payload.intent as Record<string, unknown> | undefined,
        effectiveFilters:
          (payload.effective_filters as SearchFilters | undefined) ?? filters,
        relaxedPreferences: readRelaxations(payload.relaxed_preferences),
        unresolvedConstraints: readUnresolved(payload.unresolved_constraints),
        facets:
          (payload.facets as Record<string, Record<string, number>>) ?? {},
        // The server decides whether this request wanted a conversation,
        // because only it can read the shopper's language. A missing or
        // unrecognised value is not quietly rewritten to `grid`: that is
        // exactly how a stale backend would revert the storefront to
        // keyword-only behaviour with nothing anywhere reporting it.
        interactionMode: readInteractionMode(payload.interaction_mode),
      }
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return {
        items: filterDemoProducts(query, filters),
        effectiveFilters: filters,
        relaxedPreferences: [],
        unresolvedConstraints: [],
        facets: {},
        // The demo catalogue is a fixture, not a judgement about the shopper.
        interactionMode: 'undetermined',
      }
    }
  },

  async recommendations(
    context?: Partial<Experience>,
    filters: SearchFilters = {},
    travellers = 0,
  ): Promise<Experience[]> {
    try {
      const params = new URLSearchParams()
      if (context?.id) params.set('experience_id', context.id)
      if (filters.destination) params.set('destination', filters.destination)
      if (filters.visit_start) params.set('visit_start', filters.visit_start)
      if (filters.visit_end) params.set('visit_end', filters.visit_end)
      if (filters.max_total_price !== undefined) {
        params.set('max_total_price', String(filters.max_total_price))
      }
      if (travellers) params.set('travellers', String(travellers))
      withDisplayCurrency(params)
      const query = params.toString() ? `?${params}` : ''
      return normalizeProducts(await request(`/recommendations${query}`))
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      const products = [...demoExperiences]
      if (context?.destination) {
        products.sort((a, b) =>
          a.destination === context.destination &&
          b.destination !== context.destination
            ? -1
            : 1,
        )
      }
      return products.slice(0, 6)
    }
  },

  async createConversation(context: {
    query?: string
    filters?: SearchFilters
    resultIds?: string[]
    partySize?: number
  } = {}): Promise<string> {
    try {
      const conversation = await request<{ id: string }>('/conversations', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({
          query: context.query,
          filters: context.filters ?? {},
          result_ids: context.resultIds ?? [],
          party: context.partySize
            ? [{ type: 'adult', count: context.partySize }]
            : [],
        }),
      })
      return conversation.id
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return `demo-${crypto.randomUUID()}`
    }
  },

  async sendMessage(
    conversationId: string,
    text: string,
    visibleProducts: Experience[],
    context: AssistantContext = {},
  ): Promise<AssistantMessage> {
    try {
      const httpResponse = await fetch(
        `${API_BASE}/conversations/${conversationId}/messages?stream=true`,
        {
          method: 'POST',
          headers: { ...jsonHeaders(), Accept: 'text/event-stream' },
          body: JSON.stringify({ message: text, context }),
        },
      )
      if (!httpResponse.ok) {
        throw new ApiRequestError(
          `Assistant request failed: ${httpResponse.status}`,
          httpResponse.status,
        )
      }
      if (!httpResponse.body) {
        throw new Error('Assistant response stream is unavailable.')
      }
      const reader = httpResponse.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let response: Record<string, unknown> | undefined
      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? ''
        for (const event of events) {
          const lines = event.split('\n')
          const eventName = lines
            .find((line) => line.startsWith('event:'))
            ?.slice(6)
            .trim()
          const data = lines
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trim())
            .join('\n')
          if (eventName === 'completed' && data) {
            response = JSON.parse(data) as Record<string, unknown>
          }
          if (eventName === 'error' && data) {
            const problem = JSON.parse(data) as Record<string, unknown>
            throw new Error(String(problem.detail ?? 'Assistant request failed.'))
          }
        }
        if (done) break
      }
      if (!response) {
        throw new Error('Assistant stream ended without a completed response.')
      }
      const products = normalizeProducts(response)
      const actions = Array.isArray(response.actions)
        ? (response.actions as Array<Record<string, unknown>>).map(
            normalizeAssistantAction,
          )
        : []
      const statePatch = (response.state_patch ?? {}) as Record<string, unknown>
      return {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: assistantMessage(
          response.message_code,
          response.message_vars,
          response.message ?? response.text,
        ),
        // The service could not reach the model, so it searched for the text as
        // written and nothing was added, prepared or booked. Saying so is the
        // difference between a degraded answer and a wrong one.
        degraded: response.degraded === true,
        products,
        actions,
        filters: statePatch.filters as SearchFilters | undefined,
        timestamp: new Date(),
      }
    } catch (error) {
      if (!conversationId.startsWith('demo-') || !ALLOW_DEMO_FALLBACK) throw error
      // This path exists so the demo renders with no backend. It deliberately
      // does not interpret the shopper's words: keyword matching on `text` was
      // a second, worse assistant that answered in English and disagreed with
      // the real one. Understanding the request is the model's job, so with no
      // model reachable this offers what is already on screen and says so.
      const matches = visibleProducts.length ? visibleProducts : demoExperiences

      return {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: { key: 'assistant.defaultReply' },
        products: matches.slice(0, 3),
        actions: matches[0]
          ? [
              {
                type: 'ADD_TO_CART',
                label: {
                  key: 'assistant.action.addNamed',
                  vars: { title: matches[0].title },
                },
                experience_id: matches[0].id,
              },
            ]
          : [],
        timestamp: new Date(),
      }
    }
  },

  async addCartItem(
    product: Experience,
    date: string,
    adults = 2,
    children = 0,
    selection: Pick<AssistantAction, 'option_id' | 'slot_id'> = {},
    knownProducts: Experience[] = [],
    existingItems: CartItem[] = [],
  ): Promise<CartItem[]> {
    try {
      let optionId = selection.option_id ?? product.options?.[0]?.id
      let slotId = selection.slot_id ?? product.options?.[0]?.slots?.[0]?.id
      if (!optionId || !slotId) {
        const availability = await request<Record<string, unknown>>(
          `/experiences/${product.id}/availability?visit_start=${encodeURIComponent(
            `${date}T00:00:00Z`,
          )}`,
        )
        const options = Array.isArray(availability.options)
          ? (availability.options as Array<Record<string, unknown>>)
          : []
        const option =
          options.find((candidate) => String(candidate.id) === optionId) ?? options[0]
        optionId = optionId ?? (option?.id ? String(option.id) : undefined)
        const validityType = String(
          option?.validity_type ?? product.options?.[0]?.validity_type ?? '',
        )
        const slots = Array.isArray(option?.slots)
          ? (option.slots as Array<Record<string, unknown>>)
          : []
        const slot = slots.find(
          (candidate) =>
            String(candidate.status) === 'AVAILABLE' &&
            Number(candidate.capacity_remaining ?? 0) >= adults + children,
        )
        slotId = slotId ?? (slot?.id ? String(slot.id) : undefined)
        if (!slotId && validityType !== 'OPEN_DATED') {
          throw new Error('No availability exists on the selected date.')
        }
      }
      if (!optionId) throw new Error('No active option is available.')

      const response = await request<Record<string, unknown>>('/cart/items', {
        method: 'POST',
        headers: { ...jsonHeaders(), 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          experience_id: product.id,
          option_id: optionId,
          slot_id: slotId,
          participants: [
            { type: 'adult', count: adults },
            ...(children > 0
              ? [{ type: 'child', count: children, age: 8 }]
              : []),
          ],
        }),
      })
      return normalizeCart(response, [product, ...knownProducts])
    } catch (error) {
      if (!product.id.startsWith('exp-') || !ALLOW_DEMO_FALLBACK) throw error
      return [
        ...existingItems,
        demoCartItem(product, date, adults, children),
      ]
    }
  },

  async getCart(knownProducts: Experience[] = []): Promise<CartItem[]> {
    try {
      const response = await request<Record<string, unknown>>('/cart')
      return normalizeCart(response, knownProducts)
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return []
    }
  },

  async removeCartItem(
    itemId: string,
    knownProducts: Experience[] = [],
  ): Promise<CartItem[]> {
    const response = await request<Record<string, unknown>>(
      `/cart/items/${itemId}`,
      {
        method: 'DELETE',
        headers: { ...jsonHeaders(), 'Idempotency-Key': crypto.randomUUID() },
      },
    )
    return normalizeCart(response, knownProducts)
  },

  async experience(experienceId: string): Promise<Experience | undefined> {
    try {
      return normalizeExperience(
        await request<Record<string, unknown>>(`/experiences/${experienceId}`),
      )
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return demoExperiences.find((product) => product.id === experienceId)
    }
  },

  async confirmCheckout(
    items: CartItem[],
    customer: { name: string; email: string },
    placement?: string,
  ): Promise<Voucher> {
    try {
      await request<Record<string, unknown>>(
        '/checkout/prepare',
        {
          method: 'POST',
          headers: { ...jsonHeaders(), 'Idempotency-Key': crypto.randomUUID() },
          body: JSON.stringify({}),
        },
      )
      const booking = await request<Record<string, unknown>>('/checkout/confirm', {
        method: 'POST',
        headers: { ...jsonHeaders(), 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          confirmation: 'CONFIRM',
          customer_details: customer,
          placement,
        }),
      })
      const backendVoucher = booking.voucher as Record<string, unknown>
      return {
        booking_reference: String(booking.booking_reference),
        voucher_reference: String(backendVoucher.voucher_reference),
        qr_payload: String(backendVoucher.qr_payload),
        qr_image_data_url: String(backendVoucher.qr_image_data_url),
        total: Number(booking.total),
        currency: String(booking.currency),
      }
    } catch (error) {
      if (
        !ALLOW_DEMO_FALLBACK ||
        !items.every((item) => item.experience.id.startsWith('exp-'))
      ) {
        throw error
      }
      const total = items.reduce((sum, item) => sum + item.total, 0)
      const suffix = Math.random().toString(36).slice(2, 8).toUpperCase()
      return {
        booking_reference: `VT-${suffix}`,
        voucher_reference: `VOUCHER-${suffix}`,
        qr_payload: `vietra://booking/VT-${suffix}`,
        total,
        currency: items[0]?.experience.currency ?? 'USD',
      }
    }
  },
}
