export type ExperienceOption = {
  id: string
  name: string
  price: number
  currency: string
  validity_type?: string
  start_times?: string[]
  slots?: Array<{
    id: string
    starts_at: string
    status?: string
    capacity_remaining?: number
  }>
}

export type Experience = {
  id: string
  slug: string
  title: string
  destination: string
  location: string
  category: string
  short_description: string
  description?: string
  image_url: string
  fallback_image_url?: string
  rating: number
  review_count: number
  price: number
  currency: string
  duration_minutes: number
  tags: string[]
  badges: string[]
  reason?: string
  available?: boolean
  instant_confirmation?: boolean
  free_cancellation?: boolean
  family_friendly?: boolean
  accessibility_features?: string[]
  options?: ExperienceOption[]
  actions?: AssistantAction[]
}

export type SearchFilters = {
  destination?: string
  category?: string
  visit_start?: string
  visit_end?: string
  currency?: string
  max_total_price?: number
  rating?: number
  max_duration_minutes?: number
  accessibility?: string[]
  indoor_outdoor?: string
  language?: string
  instant_confirmation?: boolean
  family_friendly?: boolean
  free_cancellation?: boolean
  exclusions?: string[]
}

export type AssistantContext = {
  query?: string
  filters?: SearchFilters
  party?: Array<{ type: string; count: number }>
  result_ids?: string[]
  result_count?: number
  recently_viewed?: string[]
  focused_experience_id?: string
  cart_experience_ids?: string[]
}

export type AssistantAction = {
  type:
    | 'VIEW'
    | 'ADD_TO_CART'
    | 'CHECK_AVAILABILITY'
    | 'START_CHECKOUT'
    | 'APPLY_FILTER'
    | 'PREPARE_CHECKOUT'
    | 'CONFIRM_SIMULATED_CHECKOUT'
    | 'VIEW_VOUCHER'
  label: string
  experience_id?: string
  option_id?: string
  slot_id?: string
  value?: string
}

export type AssistantMessage = {
  id: string
  role: 'assistant' | 'user'
  text: string
  products?: Experience[]
  actions?: AssistantAction[]
  filters?: SearchFilters
  timestamp: Date
}

export type CartItem = {
  id: string
  experience: Experience
  option_id?: string
  option_name?: string
  slot_id?: string
  date: string
  time?: string
  adults: number
  children: number
  total: number
}

export type Voucher = {
  booking_reference: string
  voucher_reference: string
  qr_payload: string
  qr_image_data_url?: string
  total: number
  currency: string
}
