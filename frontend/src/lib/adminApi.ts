// Operator console API client.
//
// Kept separate from lib/api.ts on purpose. The storefront client carries a
// shopper session and falls back to demo data when the backend is missing;
// neither is ever right for a staff tool. An operator seeing invented numbers
// because the API was unreachable is strictly worse than seeing an error.

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const KEY_STORAGE = 'vietra-admin-key'

export type Capability =
  | 'read'
  | 'catalog:write'
  | 'catalog:publish'
  | 'merchandise'
  | 'configure'
  | 'operators:manage'

export interface Principal {
  email: string
  name: string
  role: string
  capabilities: Capability[]
}

export interface CatalogRow {
  id: string
  external_id: string | null
  title: string
  slug: string
  destination: string
  category: string
  status: string
  needs_review: boolean
  review_note: string | null
  supplier: string | null
  rating: number
  review_count: number
  price: number | null
  boost: number
  pinned: boolean
  suppressed: boolean
  promotion_label: string | null
  promotion_starts_at: string | null
  promotion_ends_at: string | null
  overridden_fields: string[]
  updated_at: string | null
}

export interface CatalogPage {
  items: CatalogRow[]
  total: number
  page: number
  page_size: number
}

export interface ReviewSummary {
  needs_review: number
  by_status: Record<string, number>
  merchandised: number
}

export interface AuditEntry {
  id: string
  operator: string
  action: string
  entity_type: string
  entity_id: string
  changes: Record<string, { from: unknown; to: unknown }>
  summary: string | null
  occurred_at: string
}

export interface SettingView {
  key: string
  value: unknown
  default: unknown
  overridden: boolean
  description: string
  version: number
}

export class AdminError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

let apiKey = localStorage.getItem(KEY_STORAGE) ?? ''

export const setApiKey = (key: string) => {
  apiKey = key.trim()
  if (apiKey) localStorage.setItem(KEY_STORAGE, apiKey)
  else localStorage.removeItem(KEY_STORAGE)
}

export const getApiKey = () => apiKey

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/admin${path}`, {
    ...init,
    headers: {
      'X-API-Key': apiKey,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as Record<
      string,
      unknown
    > | null
    throw new AdminError(
      String(
        problem?.detail ?? problem?.title ?? `Request failed: ${response.status}`,
      ),
      response.status,
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const whoami = () => request<Principal>('/me')

export const fetchOverview = () => request<ReviewSummary>('/overview')

export interface CatalogFilters {
  q?: string
  status?: string
  needsReview?: boolean
  supplier?: string
  destination?: string
  promoted?: boolean
  page?: number
  pageSize?: number
}

export const fetchCatalog = (filters: CatalogFilters = {}) => {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.status) params.set('status', filters.status)
  if (filters.needsReview !== undefined)
    params.set('needs_review', String(filters.needsReview))
  if (filters.supplier) params.set('supplier', filters.supplier)
  if (filters.destination) params.set('destination', filters.destination)
  if (filters.promoted !== undefined)
    params.set('promoted', String(filters.promoted))
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.pageSize ?? 25))
  return request<CatalogPage>(`/experiences?${params.toString()}`)
}

export const fetchExperience = (id: string) =>
  request<Record<string, unknown>>(`/experiences/${id}`)

export const patchExperience = (id: string, changes: Record<string, unknown>) =>
  request<CatalogRow>(`/experiences/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })

export const clearOverride = (id: string, field: string) =>
  request<CatalogRow>(`/experiences/${id}/overrides/${encodeURIComponent(field)}`, {
    method: 'DELETE',
  })

export const changeStatus = (id: string, status: string, note?: string) =>
  request<CatalogRow>(`/experiences/${id}/status`, {
    method: 'POST',
    body: JSON.stringify({ status, note: note ?? null }),
  })

export const changeMerchandising = (
  id: string,
  changes: Record<string, unknown>,
) =>
  request<CatalogRow>(`/experiences/${id}/merchandising`, {
    method: 'POST',
    body: JSON.stringify(changes),
  })

export const fetchAudit = (entityId?: string, limit = 50) => {
  const params = new URLSearchParams({ limit: String(limit) })
  if (entityId) params.set('entity_id', entityId)
  return request<{ entries: AuditEntry[] }>(`/audit?${params.toString()}`)
}

export const fetchSettings = () =>
  request<{ settings: SettingView[] }>('/settings')

export const saveSetting = (key: string, value: unknown) =>
  request<SettingView>(`/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })

export const resetSetting = (key: string) =>
  request<SettingView>(`/settings/${key}`, { method: 'DELETE' })

export interface FunnelView {
  totals: {
    experience_impression: number
    experience_viewed: number
    cart_item_added: number
    checkout_started: number
    booking_completed: number
  }
  assistant: {
    touched_sessions: number
    untouched_sessions: number
    touched_conversion: number | null
    untouched_conversion: number | null
    holdout_rate: number
  }
  search_health: {
    searches: number
    zero_results: number
    zero_result_rate: number | null
    relaxed_recoveries: number
    recovery_rate: number | null
  }
  cost: {
    day: string
    spent_usd: number
    budget_usd: number
    calls: number
    by_purpose: Record<string, number>
    breaker_tripped: boolean
  }
}

export const fetchFunnel = async () => {
  // Not under /admin: the funnel predates the console and the storefront's
  // own telemetry writes to it. It now requires the same credential to read.
  const response = await fetch(`${API_BASE}/analytics/funnel`, {
    headers: { 'X-API-Key': apiKey },
  })
  if (!response.ok) {
    throw new AdminError(`Funnel unavailable: ${response.status}`, response.status)
  }
  return (await response.json()) as FunnelView
}
