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
  Minus,
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
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import { AssistantPanel } from './components/AssistantPanel'
import { CartDrawer } from './components/CartDrawer'
import { CheckoutModal } from './components/CheckoutModal'
import { ProductCard } from './components/ProductCard'
import { VoiceInputButton } from './components/VoiceInputButton'
import { categories, demoExperiences } from './data/demo'
import { api } from './lib/api'
import type {
  AssistantAction,
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

const money = (currency: string, amount: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

function App() {
  const [products, setProducts] = useState<Experience[]>(demoExperiences)
  const [recommendations, setRecommendations] =
    useState<Experience[]>(demoExperiences.slice(0, 6))
  const [query, setQuery] = useState('')
  const [destination, setDestination] = useState('Central Vietnam')
  const [date, setDate] = useState('2026-08-15')
  const [travellers, setTravellers] = useState(2)
  const [category, setCategory] = useState('All')
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [messages, setMessages] = useState<AssistantMessage[]>([
    initialMessage,
  ])
  const [conversationId, setConversationId] = useState<string>()
  const [cartOpen, setCartOpen] = useState(false)
  const [cartItems, setCartItems] = useState<CartItem[]>([])
  const [checkoutOpen, setCheckoutOpen] = useState(false)
  const [voucher, setVoucher] = useState<Voucher | null>(null)
  const [selectedProduct, setSelectedProduct] = useState<Experience | null>(
    null,
  )
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  useEffect(() => {
    void (async () => {
      const [experiences, recommended] = await Promise.all([
        api.listExperiences(),
        api.recommendations(),
      ])
      if (experiences.length) setProducts(experiences)
      if (recommended.length) setRecommendations(recommended)
      const cart = await api.getCart([...experiences, ...recommended])
      if (cart) setCartItems(cart)
    })()
  }, [])

  const visibleProducts = useMemo(() => {
    if (category === 'All') return products
    return products.filter((product) => product.category === category)
  }, [category, products])

  const cartTotal = cartItems.reduce((sum, item) => sum + item.total, 0)

  const runSearch = async (
    searchQuery = query,
    extraFilters: SearchFilters = {},
  ) => {
    setSearching(true)
    setHasSearched(true)
    const filters: SearchFilters = {
      date,
      category: category === 'All' ? undefined : category,
      ...extraFilters,
    }
    const result = await api.search(searchQuery, filters)
    setProducts(result.items)
    setSearching(false)

    if (searchQuery.trim().split(/\s+/).length >= 4) {
      const best = result.items.slice(0, 3)
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: best.length
            ? `I translated “${searchQuery}” into a few practical preferences. These have the strongest overall fit; I can compare them or shape them into a half-day plan.`
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
      setAssistantOpen(true)
    }
  }

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    void runSearch()
  }

  const chooseSuggestion = (suggestion: string) => {
    setQuery(suggestion)
    void runSearch(suggestion)
  }

  const addToCart = async (
    product: Experience,
    fromAssistant = false,
    selection: Pick<AssistantAction, 'option_id' | 'slot_id'> = {},
  ) => {
    if (cartItems.some((item) => item.experience.id === product.id)) {
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
      setAssistantOpen(true)
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

  const sendAssistantMessage = async (text: string) => {
    const userMessage: AssistantMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      text,
      timestamp: new Date(),
    }
    setMessages((current) => [...current, userMessage])
    setAssistantBusy(true)
    try {
      const id = conversationId ?? (await api.createConversation())
      if (!conversationId) setConversationId(id)
      const response = await api.sendMessage(id, text, visibleProducts)
      setMessages((current) => [...current, response])
      const cart = await api.getCart([
        ...(response.products ?? []),
        ...visibleProducts,
        ...recommendations,
      ])
      if (cart) setCartItems(cart)
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
      setAssistantOpen(true)
    }
  }

  const confirmCheckout = async (customer: {
    name: string
    email: string
  }) => {
    const confirmation = await api.confirmCheckout(cartItems, customer)
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
    <div className="app-shell">
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
          <button onClick={() => setAssistantOpen(true)}>
            <Sparkles size={15} />
            Ask Mai
          </button>
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
              <button
                type="button"
                className="search-segment destination-segment"
                onClick={() =>
                  setDestination((value) =>
                    value === 'Central Vietnam' ? 'Hoi An' : 'Central Vietnam',
                  )
                }
              >
                <MapPin size={18} />
                <span>
                  <small>Destination</small>
                  <strong>{destination}</strong>
                </span>
              </button>
              <label className="search-segment">
                <CalendarDays size={18} />
                <span>
                  <small>Date</small>
                  <strong>{formatDate(date)}</strong>
                  <input
                    type="date"
                    value={date}
                    onChange={(event) => setDate(event.target.value)}
                    aria-label="Visit date"
                  />
                </span>
              </label>
              <button
                type="button"
                className="search-segment"
                onClick={() =>
                  setTravellers((value) => (value === 6 ? 1 : value + 1))
                }
              >
                <Users size={18} />
                <span>
                  <small>Guests</small>
                  <strong>{travellers} travellers</strong>
                </span>
              </button>
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
            <button className="assistant-cta" onClick={() => setAssistantOpen(true)}>
              <span>
                <Sparkles size={18} />
              </span>
              <div>
                <small>Not sure where to begin?</small>
                <strong>Let Mai curate your day</strong>
              </div>
              <ArrowRight size={18} />
            </button>
          </div>

          <div className="filter-toolbar">
            <div className="category-tabs">
              {categories.map((item) => (
                <button
                  className={category === item ? 'active' : ''}
                  key={item}
                  onClick={() => setCategory(item)}
                >
                  {item}
                </button>
              ))}
            </div>
            <button className="filter-button">
              <Filter size={16} />
              All filters
              <span>3</span>
            </button>
          </div>

          {searching ? (
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
              {visibleProducts.slice(0, 8).map((product) => (
                <ProductCard
                  key={product.id}
                  product={product}
                  onView={setSelectedProduct}
                  onAdd={(item) => void addToCart(item)}
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
              <button onClick={() => setAssistantOpen(true)}>Ask Mai to help</button>
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
                <button onClick={() => setSelectedProduct(product)}>
                  <ArrowRight size={17} />
                </button>
              </div>
            ))}
          </div>
        </section>

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
            <button className="primary-button" onClick={() => setAssistantOpen(true)}>
              <Bot size={18} />
              Start planning with Mai
            </button>
          </div>
        </section>
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

      {!assistantOpen && (
        <button
          className="assistant-fab"
          onClick={() => setAssistantOpen(true)}
        >
          <span>
            <Sparkles size={20} />
          </span>
          <div>
            <small>Need a thoughtful recommendation?</small>
            <strong>Ask Mai</strong>
          </div>
          <ArrowRight size={18} />
        </button>
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
        onView={setSelectedProduct}
        onAdd={(product) => void addToCart(product, true)}
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
