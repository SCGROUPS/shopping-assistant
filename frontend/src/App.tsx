import {
  ArrowRight,
  Bot,
  CalendarDays,
  Check,
  ChevronDown,
  Clock3,
  Compass,
  Filter,
  Globe2,
  Heart,
  MapPin,
  Menu,
  MessageCircle,
  Minus,
  Plus,
  Search,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Star,
  TicketCheck,
  Users,
  WandSparkles,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import { AssistantPanel } from './components/AssistantPanel'
import { CartDrawer } from './components/CartDrawer'
import { CheckoutModal } from './components/CheckoutModal'
import { ProductCard } from './components/ProductCard'
import { VoiceInputButton } from './components/VoiceInputButton'
import { categories, demoExperiences } from './data/demo'
import { api } from './lib/api'
import {
  detectFriction,
  findScheduleClash,
  isConversationalQuery,
  type FrictionSignal,
  type Nudge,
} from './lib/presence'
import type {
  AssistantAction,
  AssistantContext,
  AssistantMessage,
  CartItem,
  Experience,
  SearchFilters,
  Voucher,
} from './types'

const initialMessage: AssistantMessage = {
  id: 'welcome',
  role: 'assistant',
  text: 'Xin chào! I can turn a few preferences into a thoughtful Central Vietnam plan. I will check timing, travel fit, and availability before you book.',
  products: [demoExperiences[0], demoExperiences[1], demoExperiences[5]],
  actions: [
    {
      type: 'APPLY_FILTER',
      label: 'Show family favourites',
      value: 'family',
    },
  ],
  timestamp: new Date(),
}

const suggestionQueries = [
  'A magical Hoi An evening',
  'Family day near Da Nang',
  'Food, culture, and no rushing',
  'Rainy-day experiences',
]

const formatDate = (date: string) => {
  if (!date) return 'Choose date'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
  }).format(new Date(`${date}T12:00:00`))
}

const ANY_DESTINATION = 'Anywhere in Vietnam'

const isoDay = (offsetDays: number) =>
  new Date(Date.now() + offsetDays * 86_400_000).toISOString().slice(0, 10)

// The seeded catalogue publishes slots for today+1 .. today+30, so a fixed
// default date would silently fall outside every availability window.
const defaultVisitDate = () => isoDay(7)

type AdvancedFilters = {
  maxTotalPrice?: number
  rating?: number
  maxDurationMinutes?: number
  indoorOutdoor?: string
  language?: string
  instantConfirmation: boolean
  freeCancellation: boolean
  familyFriendly: boolean
  accessibility: string[]
}

const emptyAdvanced: AdvancedFilters = {
  instantConfirmation: false,
  freeCancellation: false,
  familyFriendly: false,
  accessibility: [],
}

const durationChoices = [
  { label: 'Up to 2 hours', value: 120 },
  { label: 'Up to 4 hours', value: 240 },
  { label: 'Up to a full day', value: 600 },
]

const ratingChoices = [4.0, 4.5, 4.8]

const accessibilityChoices = [
  'wheelchair',
  'step-free',
  'audio guide',
  'sign language',
]

const money = (currency: string, amount: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

function App() {
  const [products, setProducts] = useState<Experience[]>(
    api.demoFallbackEnabled ? demoExperiences : [],
  )
  const [recommendations, setRecommendations] = useState<Experience[]>(
    api.demoFallbackEnabled ? demoExperiences.slice(0, 6) : [],
  )
  const [query, setQuery] = useState('')
  const [destination, setDestination] = useState(ANY_DESTINATION)
  const [date, setDate] = useState(defaultVisitDate())
  const [travellers, setTravellers] = useState(2)
  const [category, setCategory] = useState('All')
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [messages, setMessages] = useState<AssistantMessage[]>([
    api.demoFallbackEnabled
      ? initialMessage
      : { ...initialMessage, products: undefined },
  ])
  const [bootstrapping, setBootstrapping] = useState(true)
  const [conversationId, setConversationId] = useState<string>()
  const [cartOpen, setCartOpen] = useState(false)
  const [cartItems, setCartItems] = useState<CartItem[]>([])
  const [checkoutOpen, setCheckoutOpen] = useState(false)
  const [voucher, setVoucher] = useState<Voucher | null>(null)
  const [selectedProduct, setSelectedProduct] = useState<Experience | null>(
    null,
  )
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [appError, setAppError] = useState('')
  const [searchContext, setSearchContext] = useState<{
    query: string
    filters: SearchFilters
    resultIds: string[]
  }>({ query: '', filters: {}, resultIds: [] })
  const viewedIds = useRef<string[]>([])
  const [relaxedPreferences, setRelaxedPreferences] = useState<string[]>([])
  const [facets, setFacets] = useState<Record<string, Record<string, number>>>(
    {},
  )
  const [filterPanelOpen, setFilterPanelOpen] = useState(false)
  const [advanced, setAdvanced] = useState<AdvancedFilters>(emptyAdvanced)
  const [refinements, setRefinements] = useState(0)
  const [viewedCount, setViewedCount] = useState(0)
  const [dismissedNudges, setDismissedNudges] = useState<FrictionSignal[]>([])
  const [openedSignals, setOpenedSignals] = useState<FrictionSignal[]>([])
  // Holdout cohort: the control group for "does guided selling actually sell
  // better than manual search". Assumed enabled until the server says
  // otherwise, so a telemetry outage never silently removes the assistant.
  const [assistantEnabled, setAssistantEnabled] = useState(true)

  const liveFilters = (): SearchFilters => {
    const filters: SearchFilters = {
      destination: destination === ANY_DESTINATION ? undefined : destination,
      visit_start: date ? `${date}T00:00:00Z` : undefined,
      category: category === 'All' ? undefined : category,
    }
    if (advanced.maxTotalPrice) filters.max_total_price = advanced.maxTotalPrice
    if (advanced.rating) filters.rating = advanced.rating
    if (advanced.maxDurationMinutes)
      filters.max_duration_minutes = advanced.maxDurationMinutes
    if (advanced.indoorOutdoor) filters.indoor_outdoor = advanced.indoorOutdoor
    if (advanced.language) filters.language = advanced.language
    if (advanced.instantConfirmation) filters.instant_confirmation = true
    if (advanced.freeCancellation) filters.free_cancellation = true
    if (advanced.familyFriendly) filters.family_friendly = true
    if (advanced.accessibility.length)
      filters.accessibility = advanced.accessibility
    return filters
  }

  const activeFilterCount =
    (destination === ANY_DESTINATION ? 0 : 1) +
    (category === 'All' ? 0 : 1) +
    Object.entries(advanced).filter(([key, value]) => {
      if (key === 'accessibility') return (value as string[]).length > 0
      return Boolean(value)
    }).length

  useEffect(() => {
    void (async () => {
      try {
        const [experiences, recommended, cohort] = await Promise.all([
          api.listExperiences(),
          api.recommendations(),
          api.sessionContext(),
        ])
        setAssistantEnabled(cohort.assistantEnabled)
        if (experiences.length) setProducts(experiences)
        if (recommended.length) setRecommendations(recommended)
        setMessages((current) =>
          current.map((message) =>
            message.id === 'welcome'
              ? { ...message, products: experiences.slice(0, 3) }
              : message,
          ),
        )
        setCartItems(await api.getCart([...experiences, ...recommended]))
      } catch {
        setAppError('The live Vietra service is unavailable. Please try again shortly.')
      } finally {
        setBootstrapping(false)
      }
    })()
  }, [])

  useEffect(() => {
    if (!hasSearched) return
    void (async () => {
      try {
        setRecommendations(
          await api.recommendations(undefined, liveFilters(), travellers),
        )
      } catch {
        // A stale rail is worse than a short one; leave the previous state.
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasSearched, destination, date, travellers])

  const visibleProducts = useMemo(() => {
    // Once a search has run the backend has already applied the category
    // filter; filtering again client-side would hide relaxed results.
    if (hasSearched || category === 'All') return products
    return products.filter((product) => product.category === category)
  }, [category, hasSearched, products])

  const destinationOptions = useMemo(() => {
    const names = new Set<string>()
    for (const product of [...products, ...recommendations]) {
      if (product.destination) names.add(product.destination)
    }
    return [ANY_DESTINATION, ...[...names].sort()]
  }, [products, recommendations])

  // Tabs follow the live catalogue rather than the demo labels, so a tab can
  // never point at a category the backend has nothing to return for.
  const categoryTabs = useMemo(() => {
    const counts = facets.category
    if (!counts || Object.keys(counts).length === 0) return categories
    const ranked = Object.entries(counts)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([name]) => name)
    if (category !== 'All' && !ranked.includes(category)) ranked.push(category)
    return ['All', ...ranked]
  }, [category, facets])

  const cartTotal = cartItems.reduce((sum, item) => sum + item.total, 0)

  const conversationStarted = messages.some(
    (message) => message.role === 'user',
  )

  const viewProduct = (product: Experience | null, surface = 'grid') => {
    if (product && !viewedIds.current.includes(product.id)) {
      viewedIds.current = [...viewedIds.current, product.id].slice(-20)
      setViewedCount((count) => count + 1)
    }
    if (product) {
      api.track('experience_viewed', {}, {
        experienceId: product.id,
        placement: surface,
      })
    }
    // Opening a result counts as engagement, so the refinement-loop nudge
    // only fires for shoppers who are cycling filters without ever clicking.
    if (product) setRefinements(0)
    setSelectedProduct(product)
  }

  const nudge = useMemo<Nudge | null>(() => {
    if (assistantOpen || !assistantEnabled) return null
    const detected = detectFriction({
      hasSearched,
      resultCount: products.length,
      refinementsSinceEngagement: refinements,
      viewedCount,
      cartItems,
      checkoutOpen,
    })
    if (!detected || dismissedNudges.includes(detected.signal)) return null
    return detected
  }, [
    assistantEnabled,
    assistantOpen,
    cartItems,
    checkoutOpen,
    dismissedNudges,
    hasSearched,
    products.length,
    refinements,
    viewedCount,
  ])

  useEffect(() => {
    if (nudge) api.track('assistant_nudge_shown', { trigger: nudge.signal })
  }, [nudge])

  const openAssistant = (reason?: Nudge) => {
    if (!assistantEnabled) return
    setAssistantOpen(true)
    api.track('assistant_opened', { trigger: reason?.signal ?? 'manual' })
    if (reason) api.track('assistant_nudge_accepted', { trigger: reason.signal })
    if (!reason || openedSignals.includes(reason.signal)) return
    setOpenedSignals((current) => [...current, reason.signal])
    // Reflect the observed context once. Repeating it reads as surveillance.
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: reason.opener,
        timestamp: new Date(),
      },
    ])
    if (reason.prompt) void sendAssistantMessage(reason.prompt)
  }

  const runSearch = async (
    searchQuery = query,
    extraFilters: SearchFilters = {},
    announce = true,
  ) => {
    setSearching(true)
    setHasSearched(true)
    const filters: SearchFilters = { ...liveFilters(), ...extraFilters }
    try {
      const result = await api.search(searchQuery, filters, travellers)
      api.track('search_submitted', {
        result_count: result.items.length,
        conversational: isConversationalQuery(searchQuery),
      })
      if (result.items.length === 0) api.track('search_zero_results', {})
      if (result.relaxedPreferences.length > 0) {
        api.track('search_relaxed', { relaxed: result.relaxedPreferences })
      }
      setProducts(result.items)
      setRelaxedPreferences(result.relaxedPreferences)
      setFacets(result.facets)
      setSearchContext({
        query: searchQuery,
        filters: result.effectiveFilters,
        resultIds: result.items.map((item) => item.id),
      })
      setConversationId(undefined)
      setAppError('')
      if (announce) setRefinements((count) => count + 1)

      // The one sanctioned auto-open: a conversational query is an explicit
      // request for help, not an unprompted interruption. Keyword queries
      // always stay in the grid.
      if (announce && isConversationalQuery(searchQuery)) {
        const best = result.items.slice(0, 3)
        const relaxed = result.relaxedPreferences.length
          ? ` I relaxed ${result.relaxedPreferences.join(', ')} to keep these bookable.`
          : ''
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            text: best.length
              ? `I translated “${searchQuery}” into a few practical preferences. These have the strongest overall fit; I can compare them or shape them into a half-day plan.${relaxed}`
              : `I could not find a live match for “${searchQuery}”. Try relaxing the destination, date, or activity preferences and I will search again.`,
            products: best,
            actions: best[0]
              ? [
                  {
                    type: 'ADD_TO_CART',
                    label: `Reserve ${best[0].title}`,
                    experience_id: best[0].id,
                  },
                ]
              : [],
            timestamp: new Date(),
          },
        ])
        openAssistant()
      }
    } catch {
      setAppError('Search could not reach the live catalog. Your current results are unchanged.')
    } finally {
      setSearching(false)
    }
  }

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    void runSearch()
  }

  useEffect(() => {
    if (!hasSearched) return
    void runSearch(searchContext.query, {}, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, advanced])

  const chooseSuggestion = (suggestion: string) => {
    setQuery(suggestion)
    void runSearch(suggestion)
  }

  const addToCart = async (
    product: Experience,
    fromAssistant = false,
    selection: Pick<AssistantAction, 'option_id' | 'slot_id'> = {},
  ) => {
    // A cart line is (experience, option, date/slot): the same tour on another
    // date is a different, bookable line rather than a duplicate.
    const alreadyBooked = cartItems.some(
      (item) =>
        item.experience.id === product.id &&
        (!selection.option_id || item.option_id === selection.option_id) &&
        (selection.slot_id
          ? item.slot_id === selection.slot_id
          : item.date === date),
    )
    if (alreadyBooked) {
      setSelectedProduct(null)
      if (!fromAssistant) setCartOpen(true)
      return
    }
    try {
      const cart = await api.addCartItem(
        product,
        date,
        travellers,
        0,
        selection,
        [...products, ...recommendations],
        cartItems,
      )
      setCartItems(cart)
      api.track(
        'cart_item_added',
        { total: product.price * travellers },
        {
          experienceId: product.id,
          placement: fromAssistant ? 'assistant' : 'grid',
        },
      )
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: `I could not reserve ${product.title} because its availability changed. Please choose another time or experience.`,
          timestamp: new Date(),
        },
      ])
      openAssistant()
      return
    }
    setSelectedProduct(null)
    if (fromAssistant) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: `${product.title} is in your trip. I rechecked the selected option and price. You can review the complete booking without leaving our conversation.`,
          products: [product],
          actions: [
            {
              type: 'START_CHECKOUT',
              label: 'Review and purchase',
            },
          ],
          timestamp: new Date(),
        },
      ])
    } else {
      setCartOpen(true)
    }
  }

  const assistantContext = (focused?: Experience): AssistantContext => ({
    query: searchContext.query || query,
    filters: { ...liveFilters(), ...searchContext.filters },
    party: [{ type: 'adult', count: travellers }],
    result_ids: visibleProducts.slice(0, 12).map((item) => item.id),
    result_count: products.length,
    recently_viewed: viewedIds.current.slice(-8),
    focused_experience_id: focused?.id,
    cart_experience_ids: cartItems.map((item) => item.experience.id),
  })

  const applyAssistantFilters = (filters?: SearchFilters) => {
    if (!filters) return
    if (filters.destination && filters.destination !== destination) {
      setDestination(filters.destination)
    }
    if (filters.visit_start) {
      const nextDate = filters.visit_start.slice(0, 10)
      if (nextDate !== date) setDate(nextDate)
    }
    if (filters.category && filters.category !== category) {
      setCategory(filters.category)
    }
  }

  const sendAssistantMessage = async (text: string, focused?: Experience) => {
    const userMessage: AssistantMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      text,
      timestamp: new Date(),
    }
    setMessages((current) => [...current, userMessage])
    setAssistantBusy(true)
    try {
      const id =
        conversationId ??
        (await api.createConversation({
          query: searchContext.query,
          filters: searchContext.filters,
          resultIds: searchContext.resultIds,
          partySize: travellers,
        }))
      if (!conversationId) setConversationId(id)
      const response = await api.sendMessage(
        id,
        text,
        visibleProducts,
        assistantContext(focused),
      )
      setMessages((current) => [...current, response])
      applyAssistantFilters(response.filters)
      const cart = await api.getCart([
        ...(response.products ?? []),
        ...visibleProducts,
        ...recommendations,
      ])
      setCartItems(cart)
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: 'I could not reach the live catalog just now. Your cart is unchanged, so please try that request again.',
          timestamp: new Date(),
        },
      ])
    } finally {
      setAssistantBusy(false)
    }
  }

  // Subject-carrying entry points (§8.4): the question already has an object,
  // so the assistant never has to ask "which one?".
  const askAboutProduct = (product: Experience) => {
    openAssistant()
    void sendAssistantMessage(`Tell me more about ${product.title}.`, product)
  }

  const checkMyPlan = () => {
    setCartOpen(false)
    openAssistant()
    void sendAssistantMessage(
      'Check my plan: does the timing work, and is anything missing?',
    )
  }

  const handleAssistantAction = async (
    action: AssistantAction,
    actionProducts: Experience[] = [],
  ) => {
    const knownProducts = [
      ...actionProducts,
      ...messages.flatMap((message) => message.products ?? []),
      ...products,
      ...recommendations,
      ...demoExperiences,
    ]
    const resolveProduct = async () =>
      knownProducts.find((item) => item.id === action.experience_id) ??
      (action.experience_id
        ? await api.experience(action.experience_id)
        : undefined)

    if (action.type === 'ADD_TO_CART' && action.experience_id) {
      const product = await resolveProduct()
      if (product) {
        await addToCart(product, true, {
          option_id: action.option_id,
          slot_id: action.slot_id,
        })
      }
      return
    }
    if (
      action.type === 'START_CHECKOUT' ||
      action.type === 'PREPARE_CHECKOUT' ||
      action.type === 'CONFIRM_SIMULATED_CHECKOUT'
    ) {
      setAssistantOpen(false)
      setCartOpen(false)
      setCheckoutOpen(true)
      return
    }
    if (action.type === 'APPLY_FILTER' && action.value === 'family') {
      setAssistantOpen(false)
      void runSearch('family-friendly experiences', {
        family_friendly: true,
      })
      return
    }
    if (
      action.type === 'CHECK_AVAILABILITY' &&
      action.experience_id
    ) {
      const product = await resolveProduct()
      if (product) setSelectedProduct(product)
      return
    }
    if (action.type === 'VIEW_VOUCHER' && voucher) {
      setAssistantOpen(false)
      setCheckoutOpen(true)
      return
    }
    if (action.experience_id) {
      const product = await resolveProduct()
      if (product) setSelectedProduct(product)
    }
  }

  const removeCartItem = async (itemId: string) => {
    const item = cartItems.find((candidate) => candidate.id === itemId)
    if (!item) return
    if (item.experience.id.startsWith('exp-')) {
      setCartItems((current) => current.filter((candidate) => candidate.id !== itemId))
      return
    }
    try {
      const cart = await api.removeCartItem(itemId, [
        ...products,
        ...recommendations,
      ])
      setCartItems(cart)
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: `I could not remove ${item.experience.title}. Refresh the cart and try again.`,
          timestamp: new Date(),
        },
      ])
      openAssistant()
    }
  }

  const confirmCheckout = async (customer: {
    name: string
    email: string
  }) => {
    const confirmation = await api.confirmCheckout(cartItems, customer)
    // Attribute the booking to the surface that sourced the cart, so
    // "the assistant converts better" becomes a measurable claim.
    api.track('booking_completed', { items: cartItems.length }, {
      placement: conversationStarted ? 'assistant' : 'grid',
    })
    setVoucher(confirmation)
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: `Booked! Your reference is ${confirmation.booking_reference}. I kept all vouchers together so they are easy to find on the day.`,
        timestamp: new Date(),
      },
    ])
  }

  return (
    <div className={`app-shell ${assistantOpen ? 'assistant-docked' : ''}`}>
      {appError && (
        <div className="service-error" role="alert">
          {appError}
          <button onClick={() => setAppError('')} aria-label="Dismiss service error">
            <X size={16} />
          </button>
        </div>
      )}
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Vietra home">
          <span className="brand-mark">V</span>
          <span>
            <strong>VIETRA</strong>
            <small>Vietnam, beautifully planned</small>
          </span>
        </a>

        <nav className={mobileMenuOpen ? 'mobile-open' : ''}>
          <a href="#discover">Discover</a>
          <a href="#recommendations">Curated for you</a>
          {assistantEnabled && (
            <button onClick={() => openAssistant()}>
              <Sparkles size={15} />
              Ask Mai
            </button>
          )}
        </nav>

        <div className="header-actions">
          <button className="currency-button">
            <Globe2 size={16} />
            USD
            <ChevronDown size={14} />
          </button>
          <button
            className="cart-button"
            onClick={() => setCartOpen(true)}
            aria-label="Open cart"
          >
            <ShoppingBag size={19} />
            {cartItems.length > 0 && <span>{cartItems.length}</span>}
          </button>
          <button
            className="menu-button"
            onClick={() => setMobileMenuOpen((value) => !value)}
            aria-label="Menu"
          >
            <Menu size={21} />
          </button>
        </div>
      </header>

      <main id="top">
        <section className="hero-section">
          <div className="hero-image" aria-hidden="true">
            <img
              src="/assets/catalog/hoi-an-lantern-evening.webp"
              onError={(event) => {
                event.currentTarget.src =
                  demoExperiences[0].fallback_image_url ?? ''
              }}
              alt=""
            />
            <span />
          </div>
          <div className="hero-orb orb-one" />
          <div className="hero-orb orb-two" />
          <div className="hero-content">
            <span className="hero-kicker">
              <WandSparkles size={15} />
              Thoughtful adventures, matched to you
            </span>
            <h1>
              Find your own rhythm
              <em> in Vietnam.</em>
            </h1>
            <p>
              Search the classic way or describe the day you imagine. Mai will
              balance place, pace, weather, and the people you travel with.
            </p>

            <form className="discovery-bar" onSubmit={submitSearch}>
              <div className="query-field">
                <Search size={21} />
                <span>
                  <small id="trip-search-label">
                    What would make this trip memorable?
                  </small>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="A relaxed family day with food and culture…"
                    aria-labelledby="trip-search-label"
                  />
                </span>
                <VoiceInputButton
                  label="Search by voice"
                  disabled={searching}
                  onTranscript={(transcript, final) => {
                    setQuery(transcript)
                    if (final) void runSearch(transcript)
                  }}
                />
              </div>
              <label className="search-segment destination-segment">
                <MapPin size={18} />
                <span>
                  <small>Destination</small>
                  <strong>{destination}</strong>
                  <select
                    value={destination}
                    onChange={(event) => setDestination(event.target.value)}
                    aria-label="Destination"
                  >
                    {destinationOptions.map((name) => {
                      const count = facets.destination?.[name]
                      return (
                        <option key={name} value={name}>
                          {count === undefined ? name : `${name} (${count})`}
                        </option>
                      )
                    })}
                  </select>
                </span>
              </label>
              <label className="search-segment">
                <CalendarDays size={18} />
                <span>
                  <small>Date</small>
                  <strong>{formatDate(date)}</strong>
                  <input
                    type="date"
                    value={date}
                    min={isoDay(1)}
                    max={isoDay(30)}
                    onChange={(event) => setDate(event.target.value)}
                    aria-label="Visit date"
                  />
                </span>
              </label>
              <div className="search-segment guest-segment">
                <Users size={18} />
                <span>
                  <small>Guests</small>
                  <strong>
                    {travellers} {travellers === 1 ? 'traveller' : 'travellers'}
                  </strong>
                </span>
                <div className="guest-stepper">
                  <button
                    type="button"
                    aria-label="Remove a traveller"
                    disabled={travellers <= 1}
                    onClick={() =>
                      setTravellers((value) => Math.max(1, value - 1))
                    }
                  >
                    <Minus size={14} />
                  </button>
                  <button
                    type="button"
                    aria-label="Add a traveller"
                    disabled={travellers >= 12}
                    onClick={() =>
                      setTravellers((value) => Math.min(12, value + 1))
                    }
                  >
                    <Plus size={14} />
                  </button>
                </div>
              </div>
              <button className="search-submit" disabled={searching}>
                {searching ? <span className="search-loader" /> : <Search size={20} />}
                <span>Explore</span>
              </button>
            </form>

            <div className="suggestion-row">
              <span>Try</span>
              {suggestionQueries.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => chooseSuggestion(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>

          <div className="trust-strip">
            <span>
              <TicketCheck size={18} />
              Instant mobile vouchers
            </span>
            <span>
              <ShieldCheck size={18} />
              Flexible cancellation
            </span>
            <span>
              <Sparkles size={18} />
              Recommendations that explain why
            </span>
          </div>
        </section>

        <section className="discovery-section" id="discover">
          <div className="section-intro">
            <div>
              <span className="eyebrow">
                {hasSearched ? 'Your search' : 'Discover Central Vietnam'}
              </span>
              <h2>
                {hasSearched
                  ? 'Experiences shaped around your request'
                  : 'Choose the feeling, not just the ticket'}
              </h2>
              <p>
                Live availability, practical details, and honest reasons each
                experience might fit.
              </p>
            </div>
            {assistantEnabled && (
              <button className="assistant-cta" onClick={() => openAssistant()}>
                <span>
                  <Sparkles size={18} />
                </span>
                <div>
                  <small>Not sure where to begin?</small>
                  <strong>Let Mai curate your day</strong>
                </div>
                <ArrowRight size={18} />
              </button>
            )}
          </div>

          <div className="filter-toolbar">
            <div className="category-tabs">
              {categoryTabs.map((item) => {
                const count =
                  item === 'All'
                    ? Object.values(facets.category ?? {}).reduce(
                        (sum, value) => sum + value,
                        0,
                      )
                    : facets.category?.[item]
                return (
                  <button
                    className={category === item ? 'active' : ''}
                    key={item}
                    onClick={() => setCategory(item)}
                  >
                    {item}
                    {hasSearched && count !== undefined && (
                      <em className="facet-count">{count}</em>
                    )}
                  </button>
                )
              })}
            </div>
            <button
              className="filter-button"
              aria-expanded={filterPanelOpen}
              onClick={() => setFilterPanelOpen((open) => !open)}
            >
              <Filter size={16} />
              All filters
              {activeFilterCount > 0 && <span>{activeFilterCount}</span>}
            </button>
          </div>

          {filterPanelOpen && (
            <div className="filter-panel">
              <div className="filter-group">
                <h4>Budget (total for {travellers})</h4>
                <input
                  type="number"
                  min={0}
                  step={100000}
                  placeholder="No limit"
                  value={advanced.maxTotalPrice ?? ''}
                  onChange={(event) =>
                    setAdvanced((current) => ({
                      ...current,
                      maxTotalPrice: event.target.value
                        ? Number(event.target.value)
                        : undefined,
                    }))
                  }
                  aria-label="Maximum total price"
                />
              </div>
              <div className="filter-group">
                <h4>Minimum rating</h4>
                <div className="chip-row">
                  {ratingChoices.map((value) => (
                    <button
                      key={value}
                      className={advanced.rating === value ? 'active' : ''}
                      onClick={() =>
                        setAdvanced((current) => ({
                          ...current,
                          rating: current.rating === value ? undefined : value,
                        }))
                      }
                    >
                      {value.toFixed(1)}+
                    </button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <h4>Duration</h4>
                <div className="chip-row">
                  {durationChoices.map((choice) => (
                    <button
                      key={choice.value}
                      className={
                        advanced.maxDurationMinutes === choice.value
                          ? 'active'
                          : ''
                      }
                      onClick={() =>
                        setAdvanced((current) => ({
                          ...current,
                          maxDurationMinutes:
                            current.maxDurationMinutes === choice.value
                              ? undefined
                              : choice.value,
                        }))
                      }
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <h4>Setting</h4>
                <div className="chip-row">
                  {['indoor', 'outdoor'].map((value) => (
                    <button
                      key={value}
                      className={
                        advanced.indoorOutdoor === value ? 'active' : ''
                      }
                      onClick={() =>
                        setAdvanced((current) => ({
                          ...current,
                          indoorOutdoor:
                            current.indoorOutdoor === value ? undefined : value,
                        }))
                      }
                    >
                      {value === 'indoor' ? 'Indoor' : 'Outdoor'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <h4>Booking terms</h4>
                <div className="chip-row">
                  <button
                    className={advanced.instantConfirmation ? 'active' : ''}
                    onClick={() =>
                      setAdvanced((current) => ({
                        ...current,
                        instantConfirmation: !current.instantConfirmation,
                      }))
                    }
                  >
                    Instant confirmation
                  </button>
                  <button
                    className={advanced.freeCancellation ? 'active' : ''}
                    onClick={() =>
                      setAdvanced((current) => ({
                        ...current,
                        freeCancellation: !current.freeCancellation,
                      }))
                    }
                  >
                    Free cancellation
                  </button>
                  <button
                    className={advanced.familyFriendly ? 'active' : ''}
                    onClick={() =>
                      setAdvanced((current) => ({
                        ...current,
                        familyFriendly: !current.familyFriendly,
                      }))
                    }
                  >
                    Family friendly
                  </button>
                </div>
              </div>
              <div className="filter-group">
                <h4>Accessibility</h4>
                <div className="chip-row">
                  {accessibilityChoices.map((value) => (
                    <button
                      key={value}
                      className={
                        advanced.accessibility.includes(value) ? 'active' : ''
                      }
                      onClick={() =>
                        setAdvanced((current) => ({
                          ...current,
                          accessibility: current.accessibility.includes(value)
                            ? current.accessibility.filter(
                                (item) => item !== value,
                              )
                            : [...current.accessibility, value],
                        }))
                      }
                    >
                      {value}
                    </button>
                  ))}
                </div>
                <small>Accessibility needs are never relaxed.</small>
              </div>
              <button
                className="filter-reset"
                onClick={() => setAdvanced(emptyAdvanced)}
                disabled={activeFilterCount === 0}
              >
                Clear filters
              </button>
            </div>
          )}

          {relaxedPreferences.length > 0 && (
            <div className="relaxation-notice" role="status">
              <Sparkles size={16} />
              <p>
                No exact match, so we relaxed{' '}
                <strong>{relaxedPreferences.join(', ')}</strong> to keep
                bookable options on screen. Your accessibility needs were kept
                intact.
              </p>
            </div>
          )}

          {searching || bootstrapping ? (
            <div className="product-grid skeleton-grid">
              {Array.from({ length: 6 }, (_, index) => (
                <div className="skeleton-card" key={index}>
                  <i />
                  <span />
                  <span />
                  <span />
                </div>
              ))}
            </div>
          ) : (
            <div className="product-grid">
              {visibleProducts.map((product) => (
                <ProductCard
                  key={product.id}
                  product={product}
                  onView={viewProduct}
                  onAdd={(item) => void addToCart(item)}
                  onAsk={assistantEnabled ? askAboutProduct : undefined}
                />
              ))}
            </div>
          )}

          {visibleProducts.length === 0 && (
            <div className="empty-results">
              <Compass size={30} />
              <h3>No exact match yet</h3>
              <p>
                Mai can relax a preference while keeping your important
                constraints intact.
              </p>
              <button onClick={() => openAssistant(nudge ?? undefined)}>
                Ask Mai to help
              </button>
            </div>
          )}
        </section>

        <section className="recommendation-section" id="recommendations">
          <div className="recommendation-copy">
            <span className="eyebrow">A day that flows</span>
            <h2>Curated pairings, not random upsells.</h2>
            <p>
              These experiences work together by location, pace, and time of
              day. Add one and Mai will reshape the rest of your plan.
            </p>
            <div className="plan-story">
              <span>
                <i>08:30</i>
                <strong>Cook, taste, connect</strong>
                <small>Market-to-table class · Hoi An</small>
              </span>
              <b />
              <span>
                <i>16:30</i>
                <strong>Golden-hour old town</strong>
                <small>Lanterns & riverside flavours</small>
              </span>
            </div>
            <button
              className="outline-button"
              onClick={() =>
                sendAssistantMessage('Build this into a relaxed full-day plan')
              }
            >
              <Sparkles size={16} />
              Ask Mai to complete this day
            </button>
          </div>
          <div className="recommendation-cards">
            {recommendations.length === 0 && (
              <p className="recommendation-empty">
                Nothing in this destination is still bookable for your dates and
                party size. Try another day and I will rebuild the plan.
              </p>
            )}
            {recommendations.slice(0, 3).map((product, index) => (
              <div
                className={`recommendation-card card-${index + 1}`}
                key={product.id}
              >
                <img
                  src={product.image_url}
                  alt={product.title}
                  onError={(event) => {
                    if (product.fallback_image_url) {
                      event.currentTarget.src = product.fallback_image_url
                    }
                  }}
                />
                <span className="image-shade" />
                <div>
                  <small>{index === 0 ? 'Morning anchor' : index === 1 ? 'Golden hour' : 'Easy finish'}</small>
                  <strong>{product.title}</strong>
                  <span>
                    {product.rating.toFixed(1)} ★ ·{' '}
                    {money(product.currency, product.price)}
                  </span>
                </div>
                <button onClick={() => viewProduct(product)}>
                  <ArrowRight size={17} />
                </button>
              </div>
            ))}
          </div>
        </section>

        {assistantEnabled && (
          <section className="assistant-promo">
            <div className="assistant-promo-art">
              <div className="promo-phone">
                <header>
                  <span className="assistant-avatar">
                    <Sparkles size={16} />
                  </span>
                  <div>
                    <strong>Mai</strong>
                    <small>Your local curator</small>
                  </div>
                </header>
                <p>
                  Since you prefer a slower pace, I would keep Ba Na Hills as the
                  only big outing and pair it with an easy river evening.
                </p>
                <button>
                  <Check size={14} />
                  Apply this plan
                </button>
              </div>
              <span className="floating-tag tag-one">Under your budget</span>
              <span className="floating-tag tag-two">No schedule conflicts</span>
            </div>
            <div className="assistant-promo-copy">
              <span className="eyebrow">More than a chatbot</span>
              <h2>A local-minded assistant that can actually book.</h2>
              <p>
                Mai remembers your filters, explains trade-offs, checks the latest
                option and price, then turns recommendations into actions you can
                trust.
              </p>
              <ul>
                <li>
                  <Check size={16} /> Compares the details that matter to you
                </li>
                <li>
                  <Check size={16} /> Builds plans without time conflicts
                </li>
                <li>
                  <Check size={16} /> Guides you through voucher-ready checkout
                </li>
              </ul>
              <button className="primary-button" onClick={() => openAssistant()}>
                <Bot size={18} />
                Start planning with Mai
              </button>
            </div>
          </section>
        )}
      </main>

      <footer className="site-footer">
        <a className="brand footer-brand" href="#top">
          <span className="brand-mark">V</span>
          <span>
            <strong>VIETRA</strong>
            <small>Vietnam, beautifully planned</small>
          </span>
        </a>
        <p>
          A proof-of-concept tourism marketplace. Product data, availability,
          payment, and vouchers are simulated.
        </p>
        <span>Hoi An · Da Nang · Hue</span>
      </footer>

      {assistantEnabled && !assistantOpen && (
        <div className={`assistant-fab-dock ${nudge ? 'nudged' : ''}`}>
          {nudge && (
            <div className="assistant-nudge" role="status">
              <p>{nudge.label}</p>
              <button
                className="nudge-dismiss"
                aria-label="Dismiss suggestion"
                onClick={() => {
                  api.track('assistant_nudge_dismissed', {
                    trigger: nudge.signal,
                  })
                  setDismissedNudges((current) => [...current, nudge.signal])
                }}
              >
                <X size={14} />
              </button>
            </div>
          )}
          <button
            className="assistant-fab"
            onClick={() => openAssistant(nudge ?? undefined)}
          >
            <span>
              <Sparkles size={20} />
            </span>
            <div>
              <small>
                {conversationStarted
                  ? 'Pick up where you left off'
                  : 'Need a thoughtful recommendation?'}
              </small>
              <strong>{conversationStarted ? 'Continue with Mai' : 'Ask Mai'}</strong>
            </div>
            <ArrowRight size={18} />
          </button>
        </div>
      )}

      <AssistantPanel
        open={assistantOpen}
        messages={messages}
        busy={assistantBusy}
        visibleProducts={visibleProducts}
        onClose={() => setAssistantOpen(false)}
        onSend={(message) => void sendAssistantMessage(message)}
        onAction={(action, actionProducts) =>
          void handleAssistantAction(action, actionProducts)
        }
        onView={viewProduct}
      />

      <CartDrawer
        open={cartOpen}
        items={cartItems}
        onClose={() => setCartOpen(false)}
        onRemove={(id) => void removeCartItem(id)}
        onCheckout={() => {
          setCartOpen(false)
          setCheckoutOpen(true)
        }}
        onCheckPlan={assistantEnabled ? checkMyPlan : undefined}
        clashing={findScheduleClash(cartItems) !== null}
      />

      <CheckoutModal
        open={checkoutOpen}
        items={cartItems}
        voucher={voucher}
        onClose={() => {
          setCheckoutOpen(false)
          if (voucher) {
            setCartItems([])
            setVoucher(null)
          }
        }}
        onConfirm={confirmCheckout}
      />

      {selectedProduct && (
        <div className="modal-shell product-modal-shell" role="dialog" aria-modal="true">
          <button
            className="modal-backdrop"
            onClick={() => setSelectedProduct(null)}
            aria-label="Close"
          />
          <section className="product-modal">
            <button
              className="modal-close"
              onClick={() => setSelectedProduct(null)}
              aria-label="Close details"
            >
              <X size={20} />
            </button>
            <div className="product-modal-image">
              <img
                src={selectedProduct.image_url}
                alt={selectedProduct.title}
                onError={(event) => {
                  if (selectedProduct.fallback_image_url) {
                    event.currentTarget.src = selectedProduct.fallback_image_url
                  }
                }}
              />
              <span />
              <div>
                <small>{selectedProduct.category}</small>
                <strong>{selectedProduct.location}</strong>
              </div>
            </div>
            <div className="product-modal-body">
              <div className="modal-title-row">
                <div>
                  <span className="eyebrow">Chosen for your trip</span>
                  <h2>{selectedProduct.title}</h2>
                </div>
                <button className="plain-icon">
                  <Heart size={20} />
                </button>
              </div>
              <div className="modal-rating">
                <Star size={15} fill="currentColor" />
                <strong>{selectedProduct.rating.toFixed(1)}</strong>
                <span>{selectedProduct.review_count.toLocaleString()} verified guests</span>
              </div>
              <p>{selectedProduct.short_description}</p>
              <div className="reason-box">
                <Sparkles size={18} />
                <span>
                  <strong>Why Mai recommends this</strong>
                  {selectedProduct.reason}
                </span>
              </div>
              <div className="detail-grid">
                <span>
                  <Clock3 size={18} />
                  <small>Duration</small>
                  <strong>{Math.round(selectedProduct.duration_minutes / 60)} hours</strong>
                </span>
                <span>
                  <CalendarDays size={18} />
                  <small>Selected date</small>
                  <strong>{formatDate(date)}</strong>
                </span>
                <span>
                  <Users size={18} />
                  <small>Guests</small>
                  <strong>{travellers} travellers</strong>
                </span>
              </div>
              <div className="option-card">
                <span>
                  <Check size={17} />
                </span>
                <div>
                  <strong>
                    {selectedProduct.options?.[0]?.name ?? 'Standard experience'}
                  </strong>
                  <small>
                    {selectedProduct.options?.[0]?.start_times?.[0] ?? 'Flexible start'} · Instant confirmation
                  </small>
                </div>
                <b>
                  {money(selectedProduct.currency, selectedProduct.price)}
                </b>
              </div>
              <div className="modal-booking-row">
                <div>
                  <span>Total from</span>
                  <strong>
                    {money(
                      selectedProduct.currency,
                      selectedProduct.price * travellers,
                    )}
                  </strong>
                  <small>for {travellers} guests</small>
                </div>
                <button
                  className="ask-about-button"
                  onClick={() => {
                    const focus = selectedProduct
                    setSelectedProduct(null)
                    askAboutProduct(focus)
                  }}
                >
                  <MessageCircle size={18} />
                  Ask about this
                </button>
                <button
                  className="checkout-button"
                  onClick={() => void addToCart(selectedProduct)}
                >
                  <ShoppingBag size={18} />
                  Add to trip
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {cartItems.length > 0 && !cartOpen && !checkoutOpen && (
        <div className="mini-cart">
          <div className="mini-cart-images">
            {cartItems.slice(0, 3).map((item) => (
              <img
                key={item.id}
                src={item.experience.image_url}
                alt=""
                onError={(event) => {
                  if (item.experience.fallback_image_url) {
                    event.currentTarget.src = item.experience.fallback_image_url
                  }
                }}
              />
            ))}
          </div>
          <span>
            <small>{cartItems.length} experience{cartItems.length > 1 ? 's' : ''}</small>
            <strong>
              {money(cartItems[0]?.experience.currency ?? 'USD', cartTotal)}
            </strong>
          </span>
          <button onClick={() => setCartOpen(true)}>
            View trip <ArrowRight size={16} />
          </button>
        </div>
      )}

      {mobileMenuOpen && (
        <button
          className="mobile-menu-dismiss"
          onClick={() => setMobileMenuOpen(false)}
          aria-label="Close menu"
        >
          <Minus />
        </button>
      )}
    </div>
  )
}

export default App
