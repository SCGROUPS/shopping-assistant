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
  Languages,
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
import {
  SUPPORTED_CURRENCIES,
  LOCALE_NAMES,
  api,
  getDisplayCurrency,
  getPreferredLocale,
  setDisplayCurrency,
  setPreferredLocale,
} from './lib/api'
import { formatCount, formatDate as intlDate, formatMoney } from './lib/format'
import { LocaleProvider } from './lib/LocaleContext'
import { buildTranslator } from './lib/useLocale'
import { chromeReady, isFallback, resolveText, translate } from './lib/i18n'
import type { LocalizedText } from './lib/i18n'
import {
  detectFriction,
  findScheduleClash,
  isConversationalQuery,
  type FrictionSignal,
  type Nudge,
} from './lib/presence'
import { NEW_LISTING_KEY, hasReviews } from './lib/rating'
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
  text: { key: 'assistant.welcome' },
  products: [demoExperiences[0], demoExperiences[1], demoExperiences[5]],
  actions: [
    {
      type: 'APPLY_FILTER',
      label: { key: 'assistant.action.familyFavourites' },
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

const formatDate = (locale: string, date: string) => {
  if (!date) return 'Choose date'
  // Noon, so a timezone west of UTC cannot render the previous day.
  return intlDate(locale, `${date}T12:00:00`, {
    month: 'short',
    day: 'numeric',
  })
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
  { key: 'filter.duration.120', value: 120 },
  { key: 'filter.duration.240', value: 240 },
  { key: 'filter.duration.600', value: 600 },
] as const

const ratingChoices = [4.0, 4.5, 4.8]

const accessibilityChoices = [
  'wheelchair',
  'step-free',
  'audio guide',
  'sign language',
]

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
  const [currency, setCurrency] = useState(getDisplayCurrency())
  const money = (code: string, amount: number) =>
    formatMoney(locale, code, amount)
  // Seeded from the server's bootstrap, not from localStorage: a stored
  // preference for a language that has since been disabled would otherwise
  // show as selected while every string on the page arrived in English.
  const [locale, setLocale] = useState('en')
  // App renders the provider, so it sits above the context and cannot consume
  // it. Same dictionary, same locale, just built directly.
  const t = buildTranslator(locale)
  const [switchingLocale, setSwitchingLocale] = useState(false)
  const localeGeneration = useRef(0)
  const [enabledLocales, setEnabledLocales] = useState<string[]>(['en'])
  const [crossSell, setCrossSell] = useState<Experience[]>([])

  useEffect(() => {
    if (!cartOpen || cartItems.length === 0) {
      setCrossSell([])
      return
    }
    void (async () => {
      const anchor = cartItems[cartItems.length - 1].experience
      const suggestions = await api.recommendations(
        anchor,
        { ...liveFilters(), destination: anchor.destination },
        travellers,
      )
      const inCart = new Set(cartItems.map((item) => item.experience.id))
      setCrossSell(suggestions.filter((item) => !inCart.has(item.id)))
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cartOpen, cartItems, travellers])

  const changeLocale = (next: string) => {
    if (next === locale || switchingLocale) return
    const previous = locale
    const previousPreference = getPreferredLocale()
    // Every card, description and cart line is resolved server-side, so
    // nothing on screen changes language until it is refetched. The chrome is
    // deliberately *not* switched first: doing so produced a page with
    // Vietnamese buttons around English descriptions for as long as the
    // network took, which reads as a broken page rather than a loading one.
    setSwitchingLocale(true)
    setPreferredLocale(next)
    api.track('filter_applied', { requested_locale: next })

    // Two quick selections can complete out of order, and the loser would
    // overwrite the winner - leaving the app displaying a language the
    // shopper had already moved on from. Only the newest switch may commit.
    const generation = ++localeGeneration.current

    void (async () => {
      try {
        const confirmed = await api.setLocale(next)

        // Everything into locals. Not one setState until the last request has
        // landed: an `await` between two commits ends React's batch, so a
        // half-applied switch is not a race that might happen, it is a paint
        // that will. `runSearch` is deliberately not reused here - it commits
        // as it goes, and it would report a `search_submitted` the shopper
        // never performed.
        const [searched, browsed, recommended, detail] = await Promise.all([
          hasSearched
            ? api.search(query, liveFilters(), travellers)
            : Promise.resolve(null),
          hasSearched ? Promise.resolve(null) : api.listExperiences(),
          api.recommendations(),
          selectedProduct
            ? api.experience(selectedProduct.id)
            : Promise.resolve(null),
        ])
        const catalogue = searched ? searched.items : (browsed ?? [])
        // The refreshed list, not the closed-over stale one: passing
        // `products` here meant the cart resolved its titles against
        // pre-switch copies of exactly the products the shopper had seen.
        const cart = await api.getCart([...catalogue, ...recommended])

        if (generation !== localeGeneration.current) return

        // One synchronous block, so the new language arrives in a single
        // paint. Committed unconditionally: an empty result in the new locale
        // is a real answer, and keeping the old list because the new one is
        // short would leave the previous language on screen.
        setProducts(catalogue)
        if (searched) {
          setRelaxedPreferences(searched.relaxedPreferences)
          setFacets(searched.facets)
          setSearchContext({
            query,
            filters: searched.effectiveFilters,
            resultIds: searched.items.map((item) => item.id),
          })
        }
        setRecommendations(recommended)
        if (detail) setSelectedProduct(detail)
        setCartItems(cart)
        setLocale(confirmed)
      } catch {
        if (generation !== localeGeneration.current) return
        // The preference and the server session were changed before the
        // fetches; leaving them pointing at a language the page is not
        // showing would make the next request disagree with the screen.
        setPreferredLocale(previousPreference)
        try {
          // Awaited rather than fired and forgotten: `finally` re-enables the
          // switcher, and re-enabling it while the session is still set to the
          // language we failed to switch to lets the next attempt start from a
          // server state nobody has seen.
          await api.setLocale(previous)
          setAppError(translate(previous, 'error.localeSwitch'))
        } catch {
          // The rollback failed too, so the session's language is now unknown
          // and the page cannot restore it. Only a reload re-derives it from
          // the server, so say that rather than inviting another attempt.
          setAppError(translate(previous, 'error.localeSession'))
        }
      } finally {
        if (generation === localeGeneration.current) setSwitchingLocale(false)
      }
    })()
  }

  const changeCurrency = (next: string) => {
    setDisplayCurrency(next)
    setCurrency(next)
    api.track('filter_applied', { display_currency: next })
    // Conversion happens server-side, so prices on screen are stale until the
    // current view is refetched.
    void (async () => {
      if (hasSearched) {
        await runSearch(query, {}, false)
      } else {
        const experiences = await api.listExperiences()
        if (experiences.length) setProducts(experiences)
      }
      setRecommendations(
        await api.recommendations(
          selectedProduct ?? undefined,
          liveFilters(),
          travellers,
        ),
      )
    })()
  }

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

        // The backend negotiates from the browser's own `Accept-Language`, so
        // it can land on a locale that has content but no interface - a
        // Korean speaker would get Korean descriptions wrapped in English
        // chrome, with Korean filtered out of the switcher and no way back.
        // Filtering the *menu* is not enough; the resolved session has to be
        // moved too.
        let active = cohort.locale
        let catalogue = experiences
        let recommendedNow = recommended
        if (!chromeReady(active)) {
          active = await api.setLocale('en')
          setPreferredLocale(active)
          ;[catalogue, recommendedNow] = await Promise.all([
            api.listExperiences(),
            api.recommendations(),
          ])
        }
        setLocale(active)
        // The backend says which locales have content; `chromeReady` says
        // which have an interface to show it in. Offering one without the
        // other produces a page that is half translated, and the shopper
        // cannot tell that from one that is broken.
        setEnabledLocales(cohort.enabledLocales.filter(chromeReady))
        if (catalogue.length) setProducts(catalogue)
        if (recommendedNow.length) setRecommendations(recommendedNow)
        setMessages((current) =>
          current.map((message) =>
            message.id === 'welcome'
              ? { ...message, products: catalogue.slice(0, 3) }
              : message,
          ),
        )
        setCartItems(await api.getCart([...catalogue, ...recommendedNow]))
      } catch {
        setAppError(
          translate(getPreferredLocale() || 'en', 'error.unavailable'),
        )
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

  // A shopper who just searched wants the answer, not the pitch: summarise the
  // result set in one line instead of repeating the standing orientation copy,
  // and take them to the grid so it is not left below the fold.
  const resultSummary = useMemo(() => {
    const count = visibleProducts.length
    const parts = [
      count === 0
        ? 'No exact match'
        : `${count} ${count === 1 ? 'experience' : 'experiences'}`,
      destination,
      formatDate(locale, date),
      `${travellers} ${travellers === 1 ? 'traveller' : 'travellers'}`,
    ]
    return parts.join(' · ')
  }, [visibleProducts.length, destination, date, travellers, locale])

  const resultsRef = useRef<HTMLElement>(null)
  const pendingScroll = useRef(false)

  useEffect(() => {
    if (searching || !pendingScroll.current) return
    pendingScroll.current = false
    const node = resultsRef.current
    if (!node) return
    node.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth',
      block: 'start',
    })
  }, [searching, visibleProducts])

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
    if (reason.prompt) void sendAssistantMessage(resolveText(locale, reason.prompt))
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
          ? t('assistant.relaxedSuffix', {
              list: result.relaxedPreferences.join(', '),
            })
          : ''
        const announcement: LocalizedText = best.length
          ? {
              key: 'assistant.searchMatched',
              vars: { query: searchQuery, relaxed },
            }
          : { key: 'assistant.searchNoMatch', vars: { query: searchQuery } }
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            text: announcement,
            products: best,
            actions: best[0]
              ? [
                  {
                    type: 'ADD_TO_CART',
                    label: {
                      key: 'assistant.action.reserveNamed',
                      vars: { title: best[0].title },
                    },
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
      setAppError(t('error.search'))
    } finally {
      setSearching(false)
    }
  }

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    pendingScroll.current = true
    void runSearch()
  }

  useEffect(() => {
    if (!hasSearched) return
    void runSearch(searchContext.query, {}, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, advanced])

  const chooseSuggestion = (suggestion: string) => {
    setQuery(suggestion)
    pendingScroll.current = true
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
          text: {
            key: 'assistant.reserveFailed',
            vars: { title: product.title },
          },
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
          text: {
            key: 'assistant.addedToTrip',
            vars: { title: product.title },
          },
          products: [product],
          actions: [
            {
              type: 'START_CHECKOUT',
              label: { key: 'assistant.action.reviewPurchase' },
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
      // The shopper's own words: already in their language, no key exists.
      text: { raw: text },
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
          text: { key: 'assistant.catalogUnreachable' },
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
          text: {
            key: 'assistant.removeFailed',
            vars: { title: item.experience.title },
          },
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
    // Attribute the booking to the surface that sourced the cart, so
    // "the assistant converts better" becomes a measurable claim. The server
    // records one event per booked experience, so it is not duplicated here.
    const confirmation = await api.confirmCheckout(
      cartItems,
      customer,
      conversationStarted ? 'assistant' : 'grid',
    )
    setVoucher(confirmation)
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: {
          key: 'assistant.booked',
          vars: { reference: confirmation.booking_reference },
        },
        timestamp: new Date(),
      },
    ])
  }

  return (
    <LocaleProvider locale={locale}>
    <div className={`app-shell ${assistantOpen ? 'assistant-docked' : ''}`}>
      {appError && (
        <div className="service-error" role="alert">
          {appError}
          <button onClick={() => setAppError('')} aria-label={t('app.a11y.dismissError')}>
            <X size={16} />
          </button>
        </div>
      )}
      <header className="site-header">
        <a className="brand" href="#top" aria-label={t('app.a11y.home')}>
          <span className="brand-mark">V</span>
          <span>
            <strong>VIETRA</strong>
            <small>{t('app.tagline')}</small>
          </span>
        </a>

        <nav className={mobileMenuOpen ? 'mobile-open' : ''}>
          <a href="#discover">{t('app.nav.discover')}</a>
          <a href="#recommendations">{t('app.nav.curated')}</a>
          {assistantEnabled && (
            <button
              data-testid="assistant-open"
              onClick={() => openAssistant()}
            >
              <Sparkles size={15} />
              {t('app.cta.askMai')}
            </button>
          )}
        </nav>

        <div className="header-actions">
          {enabledLocales.length > 1 && (
            // Hidden entirely while only one language is enabled. A switcher
            // offering a single choice is not a control, and one offering
            // languages the catalogue has not been translated into promises
            // something every page then fails to deliver.
            <label className="currency-button">
              <Languages size={16} />
              <span className="sr-only">{t('nav.language')}</span>
              <select
                value={locale}
                onChange={(event) => changeLocale(event.target.value)}
                aria-label={t('nav.language')}
                // A stable handle for the tests. Querying this control by its
                // label cannot work: the label is one of the strings the
                // switch translates, so the selector stops matching the moment
                // the feature under test succeeds.
                data-testid="locale-switcher"
                disabled={switchingLocale}
              >
                {enabledLocales.map((code) => (
                  <option key={code} value={code}>
                    {LOCALE_NAMES[code] ?? code}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} />
            </label>
          )}
          <label className="currency-button">
            <Globe2 size={16} />
            <span className="sr-only">{t('nav.currency')}</span>
            <select
              value={currency}
              onChange={(event) => changeCurrency(event.target.value)}
              aria-label={t('nav.currency')}
            >
              {SUPPORTED_CURRENCIES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </label>
          <button
            className="cart-button"
            onClick={() => setCartOpen(true)}
            aria-label={t('nav.cart.open')}
          >
            <ShoppingBag size={19} />
            {cartItems.length > 0 && <span>{cartItems.length}</span>}
          </button>
          <button
            className="menu-button"
            onClick={() => setMobileMenuOpen((value) => !value)}
            aria-label={t('app.a11y.menu')}
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
              {t('app.hero.title')}
            </span>
            <h1>
              {t('app.hero.subtitle')}
              <em> {t('app.hero.suffix')}</em>
            </h1>
            <p>{t('app.hero.lede')}</p>

            <form className="discovery-bar" onSubmit={submitSearch}>
              <div className="query-field">
                <Search size={21} />
                <span>
                  <small id="trip-search-label">
                    {t('app.search.label')}
                  </small>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t('app.search.placeholder')}
                    aria-labelledby="trip-search-label"
                    data-testid="trip-search"
                  />
                </span>
                <VoiceInputButton
                  label={t('app.search.voice')}
                  stopLabel={t('app.search.voiceStop')}
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
                  <small>{t('app.search.destination')}</small>
                  <strong>{destination}</strong>
                  <select
                    value={destination}
                    onChange={(event) => setDestination(event.target.value)}
                    aria-label={t('app.search.destination')}
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
                  <small>{t('app.search.date')}</small>
                  <strong>{formatDate(locale, date)}</strong>
                  <input
                    type="date"
                    value={date}
                    min={isoDay(1)}
                    max={isoDay(30)}
                    onChange={(event) => setDate(event.target.value)}
                    aria-label={t('app.a11y.visitDate')}
                  />
                </span>
              </label>
              <div className="search-segment guest-segment">
                <Users size={18} />
                <span>
                  <small>{t('app.search.guests')}</small>
                  <strong>
                    {t.plural('app.travellers', travellers)}
                  </strong>
                </span>
                <div className="guest-stepper">
                  <button
                    type="button"
                    aria-label={t('app.a11y.removeTraveller')}
                    disabled={travellers <= 1}
                    onClick={() =>
                      setTravellers((value) => Math.max(1, value - 1))
                    }
                  >
                    <Minus size={14} />
                  </button>
                  <button
                    type="button"
                    aria-label={t('app.a11y.addTraveller')}
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
                <span>{t('app.search.explore')}</span>
              </button>
            </form>

            <div className="suggestion-row">
              <span>{t('app.search.try')}</span>
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
              {t('app.trust.vouchers')}
            </span>
            <span>
              <ShieldCheck size={18} />
              {t('app.trust.cancellation')}
            </span>
            <span>
              <Sparkles size={18} />
              {t('app.trust.explain')}
            </span>
          </div>
        </section>

        <section
          className={`discovery-section${hasSearched ? ' searched' : ''}`}
          id="discover"
          ref={resultsRef}
        >
          <div className="section-intro">
            <div>
              <span className="eyebrow">
                {hasSearched
                  ? t('app.results.yourSearch')
                  : t('app.results.discover')}
              </span>
              <h2>
                {hasSearched
                  ? t('app.results.shaped')
                  : t('app.results.feeling')}
              </h2>
              {hasSearched ? (
                <p className="result-summary">{resultSummary}</p>
              ) : (
                <p>{t('app.results.lede')}</p>
              )}
            </div>
            {assistantEnabled && (
              <button className="assistant-cta" onClick={() => openAssistant()}>
                <span>
                  <Sparkles size={18} />
                </span>
                <div>
                  <small>{t('app.cta.unsure')}</small>
                  <strong>{t('app.cta.letMai')}</strong>
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
              {t('app.filter.all')}
              {activeFilterCount > 0 && <span>{activeFilterCount}</span>}
            </button>
          </div>

          {filterPanelOpen && (
            <div className="filter-panel">
              <div className="filter-group">
                <h4>
                  {t('app.filter.budgetFor', {
                    count: t.plural('app.travellers', travellers),
                  })}
                </h4>
                <input
                  type="number"
                  min={0}
                  step={100000}
                  placeholder={t('app.filter.noLimit')}
                  value={advanced.maxTotalPrice ?? ''}
                  onChange={(event) =>
                    setAdvanced((current) => ({
                      ...current,
                      maxTotalPrice: event.target.value
                        ? Number(event.target.value)
                        : undefined,
                    }))
                  }
                  aria-label={t('app.a11y.maxPrice')}
                />
              </div>
              <div className="filter-group">
                <h4>{t('app.filter.minRating')}</h4>
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
                <h4>{t('app.filter.duration')}</h4>
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
                      {t(choice.key)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <h4>{t('app.filter.setting')}</h4>
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
                      {value === 'indoor'
                        ? t('app.filter.indoor')
                        : t('app.filter.outdoor')}
                    </button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <h4>{t('app.filter.terms')}</h4>
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
                    {t('app.filter.instant')}
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
                    {t('app.filter.freeCancel')}
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
                    {t('app.filter.family')}
                  </button>
                </div>
              </div>
              <div className="filter-group">
                <h4>{t('app.filter.accessibility')}</h4>
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
                <small>{t('app.filter.accessibilityNote')}</small>
              </div>
              <button
                className="filter-reset"
                onClick={() => setAdvanced(emptyAdvanced)}
                disabled={activeFilterCount === 0}
              >
                {t('app.filter.clear')}
              </button>
            </div>
          )}

          {relaxedPreferences.length > 0 && (
            <div className="relaxation-notice" role="status">
              <Sparkles size={16} />
              <p>
                {t('app.relaxed.prefix')}{' '}
                <strong>{relaxedPreferences.join(', ')}</strong>{' '}
                {t('app.relaxed.suffix')}
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
              <h3>{t('app.empty.noMatch')}</h3>
              <p>
                {t('app.relaxed.note')}
              </p>
              <button onClick={() => openAssistant(nudge ?? undefined)}>
                {t('app.cta.askHelp')}
              </button>
            </div>
          )}
        </section>

        <section className="recommendation-section" id="recommendations">
          <div className="recommendation-copy">
            <span className="eyebrow">{t('app.promo.flow')}</span>
            <h2>{t('app.promo.flowBody')}</h2>
            <p>
              {t('app.plan.lede')}
            </p>
            <div className="plan-story">
              <span>
                <i>08:30</i>
                <strong>{t('app.promo.cook')}</strong>
                <small>{t('app.plan.sample')}</small>
              </span>
              <b />
              <span>
                <i>16:30</i>
                <strong>{t('app.promo.oldTown')}</strong>
                <small>{t('app.promo.lanterns')}</small>
              </span>
            </div>
            <button
              className="outline-button"
              onClick={() =>
                sendAssistantMessage('Build this into a relaxed full-day plan')
              }
            >
              <Sparkles size={16} />
              {t('app.cta.completeDay')}
            </button>
          </div>
          <div className="recommendation-cards">
            {recommendations.length === 0 && (
              <p className="recommendation-empty">
                {t('app.empty.body')}
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
                  <small>
                    {index === 0
                      ? t('app.plan.morning')
                      : index === 1
                        ? t('app.plan.golden')
                        : t('app.plan.finish')}
                  </small>
                  <strong>{product.title}</strong>
                  <span>
                    {hasReviews(product) ? `${product.rating.toFixed(1)} ★ · ` : ''}
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
                    <strong>{t('app.assistantName')}</strong>
                    <small>{t('app.mai.role')}</small>
                  </div>
                </header>
                <p>{t('app.plan.sampleAdvice')}</p>
                <button>
                  <Check size={14} />
                  {t('app.cta.applyPlan')}
                </button>
              </div>
              <span className="floating-tag tag-one">{t('app.mai.budget')}</span>
              <span className="floating-tag tag-two">{t('app.mai.noClash')}</span>
            </div>
            <div className="assistant-promo-copy">
              <span className="eyebrow">{t('app.mai.more')}</span>
              <h2>{t('app.mai.moreBody')}</h2>
              <p>
                {t('app.assistant.lede')}
              </p>
              <ul>
                <li>
                  <Check size={16} /> {t('app.assistant.compare')}
                </li>
                <li>
                  <Check size={16} /> {t('app.assistant.plans')}
                </li>
                <li>
                  <Check size={16} /> {t('app.assistant.checkout')}
                </li>
              </ul>
              <button className="primary-button" onClick={() => openAssistant()}>
                <Bot size={18} />
                {t('app.cta.startPlanning')}
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
            <small>{t('app.tagline')}</small>
          </span>
        </a>
        <p>
          {t('app.footer.note')}
        </p>
        <span>{t('app.footer.places')}</span>
      </footer>

      {assistantEnabled && !assistantOpen && (
        <div className={`assistant-fab-dock ${nudge ? 'nudged' : ''}`}>
          {nudge && (
            <div className="assistant-nudge" role="status">
              <p>{resolveText(locale, nudge.label)}</p>
              <button
                className="nudge-dismiss"
                aria-label={t('app.a11y.dismissSuggestion')}
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
                  ? t('app.resume.title')
                  : t('app.resume.body')}
              </small>
              <strong>
                {conversationStarted
                  ? t('app.resume.continue')
                  : t('app.resume.ask')}
              </strong>
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
        crossSell={crossSell}
        onAddCrossSell={(product) => {
          api.track('recommendation_clicked', {}, {
            experienceId: product.id,
            placement: 'cart_cross_sell',
          })
          void addToCart(product)
        }}
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
            aria-label={t('app.a11y.closeDetail')}
          />
          <section className="product-modal">
            <button
              className="modal-close"
              onClick={() => setSelectedProduct(null)}
              aria-label={t('app.a11y.closeDetails')}
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
                  <span className="eyebrow">{t('app.detail.chosen')}</span>
                  <h2>{selectedProduct.title}</h2>
                </div>
                <button className="plain-icon">
                  <Heart size={20} />
                </button>
              </div>
              <div className="modal-rating">
                {hasReviews(selectedProduct) ? (
                  <>
                    <Star size={15} fill="currentColor" />
                    <strong>{selectedProduct.rating.toFixed(1)}</strong>
                    <span>
                      {t('app.detail.reviews', {
                        count: formatCount(locale, selectedProduct.review_count),
                      })}
                    </span>
                  </>
                ) : (
                  <span>
                    {t(NEW_LISTING_KEY)} · {t('app.detail.noReviews')}
                  </span>
                )}
              </div>
              <p>{selectedProduct.short_description}</p>
              {(() => {
                // Said once for the record as a whole rather than per field: a
                // detail page that repeated "not translated" beside every
                // paragraph would be noise, and the shopper's question is
                // whether this page is in their language, not which of its
                // eight strings are.
                const meta = selectedProduct.content_meta ?? {}
                const fields = Object.values(meta)
                const fallback = fields.some(isFallback)
                const stale = fields.some((field) => field.stale)
                if (!fallback && !stale) return null
                // Both, when both. They are independent facts - some of this
                // page was never translated, some was translated and has since
                // gone out of date - and a ternary that reports only the first
                // hides the second from the only person who can act on it.
                return (
                  <p className="content-provenance" role="note">
                    <Languages size={14} />
                    <span>
                      {fallback && <span>{t('content.fallback')}</span>}
                      {stale && <span>{t('content.stale')}</span>}
                    </span>
                  </p>
                )
              })()}
              <div className="reason-box">
                <Sparkles size={18} />
                <span>
                  <strong>{t('app.detail.why')}</strong>
                  {selectedProduct.reason}
                </span>
              </div>
              <div className="detail-grid">
                <span>
                  <Clock3 size={18} />
                  <small>{t('app.filter.duration')}</small>
                  <strong>
                    {t.plural(
                      'app.hours',
                      Math.round(selectedProduct.duration_minutes / 60),
                    )}
                  </strong>
                </span>
                <span>
                  <CalendarDays size={18} />
                  <small>{t('app.detail.selectedDate')}</small>
                  <strong>{formatDate(locale, date)}</strong>
                </span>
                <span>
                  <Users size={18} />
                  <small>{t('app.search.guests')}</small>
                  <strong>{t.plural('app.travellers', travellers)}</strong>
                </span>
              </div>
              <div className="option-card">
                <span>
                  <Check size={17} />
                </span>
                <div>
                  <strong>
                    {selectedProduct.options?.[0]?.name ??
                      t('app.detail.standardOption')}
                  </strong>
                  <small>
                    {selectedProduct.options?.[0]?.start_times?.[0] ??
                      t('app.detail.flexibleStart')}{' '}
                    · {t('app.detail.instant')}
                  </small>
                </div>
                <b>
                  {money(selectedProduct.currency, selectedProduct.price)}
                </b>
              </div>
              <div className="modal-booking-row">
                <div>
                  <span>{t('app.detail.totalFrom')}</span>
                  <strong>
                    {money(
                      selectedProduct.currency,
                      selectedProduct.price * travellers,
                    )}
                  </strong>
                  <small>
                    {t('app.detail.forGuests', {
                      count: t.plural('app.guests', travellers),
                    })}
                  </small>
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
                  {t('app.cta.askAbout')}
                </button>
                <button
                  className="checkout-button"
                  onClick={() => void addToCart(selectedProduct)}
                >
                  <ShoppingBag size={18} />
                  {t('app.cta.addToTrip')}
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
            <small>{t.plural('app.experiences', cartItems.length)}</small>
            <strong>
              {money(cartItems[0]?.experience.currency ?? 'USD', cartTotal)}
            </strong>
          </span>
          <button onClick={() => setCartOpen(true)}>
            {t('app.cart.viewTrip')} <ArrowRight size={16} />
          </button>
        </div>
      )}

      {mobileMenuOpen && (
        <button
          className="mobile-menu-dismiss"
          onClick={() => setMobileMenuOpen(false)}
          aria-label={t('app.a11y.closeMenu')}
        >
          <Minus />
        </button>
      )}
    </div>
    </LocaleProvider>
  )
}

export default App
