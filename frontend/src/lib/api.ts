import { demoExperiences } from '../data/demo'
import type {
  AssistantAction,
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

const jsonHeaders = {
  'Content-Type': 'application/json',
  'X-Session-ID': SESSION_ID,
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
      'X-Session-ID': SESSION_ID,
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

const actionLabels: Record<string, string> = {
  ADD_TO_CART: 'Add to trip',
  CHECK_AVAILABILITY: 'Check times',
  PREPARE_CHECKOUT: 'Review checkout',
  CONFIRM_SIMULATED_CHECKOUT: 'Confirm demo purchase',
  VIEW_VOUCHER: 'View voucher',
}

const normalizeAssistantAction = (
  action: Record<string, unknown>,
): AssistantAction => ({
  type: String(action.type) as AssistantAction['type'],
  label:
    String(action.label ?? '') ||
    actionLabels[String(action.type)] ||
    'Continue',
  experience_id: action.experience_id
    ? String(action.experience_id)
    : undefined,
  option_id: action.option_id ? String(action.option_id) : undefined,
  slot_id: action.slot_id ? String(action.slot_id) : undefined,
})

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
      name: String(option.name ?? 'Standard experience'),
      price: Number(adultPrice?.amount ?? item.price ?? 0),
      currency: String(adultPrice?.currency ?? item.currency ?? 'VND'),
      validity_type: option.validity_type
        ? String(option.validity_type)
        : undefined,
      start_times: slots.map((slot) =>
        new Date(String(slot.starts_at)).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
        }),
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
  const badges = Array.isArray(item.badges)
    ? item.badges.map(String)
    : []
  return {
    id: String(item.id ?? item.experience_id),
    slug: String(item.slug ?? item.id),
    title: String(item.title ?? 'Vietnam experience'),
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
    duration_minutes: Number(item.duration_minutes ?? 60),
    tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
    badges,
    reason: item.reason ? String(item.reason) : undefined,
    available:
      item.availability === 'AVAILABLE' ||
      badges.some((badge) => badge.toLowerCase() === 'available'),
    instant_confirmation: badges.some((badge) =>
      badge.toLowerCase().includes('instant'),
    ),
    free_cancellation: badges.some((badge) =>
      badge.toLowerCase().includes('free cancellation'),
    ),
    family_friendly: badges.some((badge) =>
      badge.toLowerCase().includes('family'),
    ),
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
    const product = productsById.get(String(item.experience_id))
    if (!product) {
      throw new Error(`Cart experience ${String(item.experience_id)} is unavailable.`)
    }
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
      time: startsAt?.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      }),
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

  async listExperiences(): Promise<Experience[]> {
    try {
      return normalizeProducts(await request('/experiences'))
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
    facets: Record<string, Record<string, number>>
  }> {
    try {
      const payload = await request<Record<string, unknown>>('/search', {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({
          query,
          filters,
          party: [{ type: 'adult', count: partySize }],
          sort: 'recommended',
          page_size: 24,
        }),
      })
      return {
        items: normalizeProducts(payload),
        intent: payload.intent as Record<string, unknown> | undefined,
        effectiveFilters:
          (payload.effective_filters as SearchFilters | undefined) ?? filters,
        relaxedPreferences: (payload.relaxed_preferences as string[]) ?? [],
        facets:
          (payload.facets as Record<string, Record<string, number>>) ?? {},
      }
    } catch (error) {
      allowDemoFallbackOrThrow(error)
      return {
        items: filterDemoProducts(query, filters),
        effectiveFilters: filters,
        relaxedPreferences: [],
        facets: {},
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
        headers: jsonHeaders,
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
          headers: { ...jsonHeaders, Accept: 'text/event-stream' },
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
        text:
          (response.message as string) ??
          (response.text as string) ??
          'I found a few experiences that fit.',
        products,
        actions,
        filters: statePatch.filters as SearchFilters | undefined,
        timestamp: new Date(),
      }
    } catch (error) {
      if (!conversationId.startsWith('demo-') || !ALLOW_DEMO_FALLBACK) throw error
      const lower = text.toLowerCase()
      let matches = visibleProducts.length ? visibleProducts : demoExperiences
      let reply =
        'I balanced your interests, travel time, availability, and guest ratings. These are the strongest options.'

      if (lower.includes('wheelchair') || lower.includes('accessible')) {
        matches = demoExperiences.filter(
          (product) => product.accessibility_features?.length,
        )
        reply =
          'These options publish accessibility details. Ba Na Hills has the strongest step-free route, while the cooking class is the easiest lower-energy choice.'
      } else if (lower.includes('family') || lower.includes('child')) {
        matches = demoExperiences.filter(
          (product) => product.family_friendly,
        )
        reply =
          'For families, I would prioritise short transfers, flexible cancellation, and experiences with natural breaks. The basket boat is the easiest win.'
      } else if (lower.includes('food') || lower.includes('eat')) {
        matches = demoExperiences.filter(
          (product) => product.category === 'Food',
        )
        reply =
          'These are the best hands-on food experiences. Choose the street-food walk for energy and variety, or the cooking class for a slower shared activity.'
      } else if (lower.includes('rain') || lower.includes('indoor')) {
        matches = demoExperiences.filter((product) =>
          ['Wellness', 'Food'].includes(product.category),
        )
        reply =
          'For wet weather, I would keep the plan flexible and mostly covered. This pairing gives you local flavour plus a restorative finish.'
      } else if (lower.includes('checkout') || lower.includes('book')) {
        reply =
          'Your selection can be reserved now. I will recheck the price and time before opening the secure demo checkout.'
      }

      return {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: reply,
        products: matches.slice(0, 3),
        actions: matches[0]
          ? [
              {
                type: 'ADD_TO_CART',
                label: `Add ${matches[0].title}`,
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
        headers: { ...jsonHeaders, 'Idempotency-Key': crypto.randomUUID() },
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
        headers: { ...jsonHeaders, 'Idempotency-Key': crypto.randomUUID() },
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
  ): Promise<Voucher> {
    try {
      await request<Record<string, unknown>>(
        '/checkout/prepare',
        {
          method: 'POST',
          headers: { ...jsonHeaders, 'Idempotency-Key': crypto.randomUUID() },
          body: JSON.stringify({}),
        },
      )
      const booking = await request<Record<string, unknown>>('/checkout/confirm', {
        method: 'POST',
        headers: { ...jsonHeaders, 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          confirmation: 'CONFIRM',
          customer_details: customer,
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
