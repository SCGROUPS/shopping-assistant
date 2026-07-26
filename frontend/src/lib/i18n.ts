// UI chrome translation.
//
// Catalogue text is translated server-side and arrives already in the
// shopper's language. This file covers the other half: the words the
// application itself says - buttons, headings, empty states, errors. They are
// static, few, and known at build time, so they do not belong in the
// translation pipeline that exists to handle a supplier writing new prose.
//
// The English dictionary is the source of truth for the *key set*. Every other
// locale is typed against it, so a missing or misspelled key is a compile
// error rather than an English word appearing in the middle of a Vietnamese
// sentence - which is the failure this file exists to prevent and the one that
// a runtime `?? fallback` would hide.

const en = {
  'nav.experiences': 'Experiences',
  'nav.cart.open': 'Open cart',
  'nav.language': 'Language',
  'nav.currency': 'Display currency',

  'cart.eyebrow': 'Your trip',
  'cart.title': 'Experience cart',
  'cart.close': 'Close cart',
  'cart.empty.title': 'Your adventure starts here',
  'cart.crossSell': 'Goes well with your day',
  'cart.cancellation': 'Free cancellation on every item',
  'cart.taxes': 'Taxes included',
  'cart.total': 'Total',
  'cart.empty.body': 'Add an experience and Mai can help complete your day.',
  'cart.assurance.recheck':
    'We recheck price and availability before booking.',
  'cart.continueCheckout': 'Continue to checkout',
  'cart.demoNote': 'No real payment will be charged in this demo.',

  'checkout.title': 'Your booking',
  'checkout.leadTraveller': 'Lead traveller',
  'checkout.emailNote': 'Your vouchers will be sent here.',
  'checkout.demoPayment': 'Demo payment',
  'checkout.demoNote': 'No card details or real money are used.',
  'checkout.wallet': 'Vietra test wallet',
  'checkout.reference': 'Booking reference',
  'checkout.close': 'Close checkout',
  'checkout.total': 'Total',

  'product.from': 'From',

  'app.tagline': 'Vietnam, beautifully planned',
  'app.nav.discover': 'Discover',
  'app.nav.curated': 'Curated for you',
  'app.search.destination': 'Destination',
  'app.search.date': 'Date',
  'app.search.guests': 'Guests',
  'app.search.explore': 'Explore',
  'app.search.try': 'Try',
  'app.cta.unsure': 'Not sure where to begin?',
  'app.cta.letMai': 'Let Mai curate your day',
  'app.filter.minRating': 'Minimum rating',
  'app.filter.duration': 'Duration',
  'app.filter.setting': 'Setting',
  'app.filter.terms': 'Booking terms',
  'app.filter.accessibility': 'Accessibility',
  'app.filter.accessibilityNote': 'Accessibility needs are never relaxed.',
  'app.empty.noMatch': 'No exact match yet',
  'app.promo.flow': 'A day that flows',
  'app.promo.flowBody': 'Curated pairings, not random upsells.',
  'app.promo.cook': 'Cook, taste, connect',
  'app.promo.oldTown': 'Golden-hour old town',
  'app.promo.lanterns': 'Lanterns & riverside flavours',
  'app.mai.role': 'Your local curator',
  'app.mai.budget': 'Under your budget',
  'app.mai.noClash': 'No schedule conflicts',
  'app.mai.more': 'More than a chatbot',
  'app.mai.moreBody': 'A local-minded assistant that can actually book.',
  'app.detail.chosen': 'Chosen for your trip',
  'app.detail.why': 'Why Mai recommends this',
  'app.detail.selectedDate': 'Selected date',
  'app.detail.totalFrom': 'Total from',
  'app.a11y.dismissError': 'Dismiss service error',
  'app.a11y.home': 'Vietra home',
  'app.a11y.menu': 'Menu',
  'app.search.placeholder': 'A relaxed family day with food and culture…',
  'app.a11y.visitDate': 'Visit date',
  'app.a11y.removeTraveller': 'Remove a traveller',
  'app.a11y.addTraveller': 'Add a traveller',
  'app.filter.noLimit': 'No limit',
  'app.a11y.maxPrice': 'Maximum total price',
  'app.a11y.dismissSuggestion': 'Dismiss suggestion',
  'app.a11y.closeDetails': 'Close details',
  'app.a11y.closeMenu': 'Close menu',
  'app.cta.askMai': 'Ask Mai',
  'app.hero.title': 'Thoughtful adventures, matched to you',
  'app.hero.subtitle': 'Find your own rhythm',
  'app.search.label': 'What would make this trip memorable?',
  'app.trust.vouchers': 'Instant mobile vouchers',
  'app.trust.cancellation': 'Flexible cancellation',
  'app.trust.explain': 'Recommendations that explain why',
  'app.filter.all': 'All filters',
  'app.filter.instant': 'Instant confirmation',
  'app.filter.freeCancel': 'Free cancellation',
  'app.filter.family': 'Family friendly',
  'app.filter.clear': 'Clear filters',
  'app.cta.askHelp': 'Ask Mai to help',
  'app.cta.completeDay': 'Ask Mai to complete this day',
  'app.cta.applyPlan': 'Apply this plan',
  'app.cta.startPlanning': 'Start planning with Mai',
  'app.cta.askAbout': 'Ask about this',
  'app.cta.addToTrip': 'Add to trip',

  'assistant.a11y.panel': 'Mai shopping assistant',
  'assistant.title': 'Mai, your local trip curator',
  'assistant.ready': 'Ready to help',
  'assistant.a11y.close': 'Close assistant',
  'assistant.availableOnDate': 'Available on your date',
  'assistant.placeholder': 'Ask about timing, access, prices, or build a plan…',
  'assistant.a11y.send': 'Send',
  'content.fallback': 'Shown in English — not yet translated.',
  'content.stale': 'This description was updated recently; the translation is catching up.',

  'error.unavailable':
    'The live Vietra service is unavailable. Please try again shortly.',
} as const

export type MessageKey = keyof typeof en

// Partial by design: a locale may be enabled before its chrome is fully
// written, and the untranslated keys then show in English - visibly incomplete
// rather than blank. `missingKeys` below is what turns that from a silent
// state into a reportable one, so the enablement gate can refuse a locale
// whose chrome is not finished.
type Dictionary = Partial<Record<MessageKey, string>>

const vi: Dictionary = {
  'nav.experiences': 'Trải nghiệm',
  'nav.cart.open': 'Mở giỏ hàng',
  'nav.language': 'Ngôn ngữ',
  'nav.currency': 'Đơn vị tiền tệ hiển thị',

  'cart.eyebrow': 'Chuyến đi của bạn',
  'cart.title': 'Giỏ trải nghiệm',
  'cart.close': 'Đóng giỏ hàng',
  'cart.empty.title': 'Hành trình của bạn bắt đầu từ đây',
  'cart.crossSell': 'Kết hợp tuyệt vời cho ngày của bạn',
  'cart.cancellation': 'Miễn phí huỷ cho mọi trải nghiệm',
  'cart.taxes': 'Đã bao gồm thuế',
  'cart.total': 'Tổng cộng',
  'cart.empty.body': 'Thêm một trải nghiệm và Mai sẽ giúp bạn hoàn thiện ngày của mình.',
  'cart.assurance.recheck':
    'Chúng tôi kiểm tra lại giá và tình trạng chỗ trước khi đặt.',
  'cart.continueCheckout': 'Tiếp tục thanh toán',
  'cart.demoNote': 'Không có khoản thanh toán thật nào được thực hiện trong bản demo này.',

  'checkout.title': 'Đặt chỗ của bạn',
  'checkout.leadTraveller': 'Khách chính',
  'checkout.emailNote': 'Voucher sẽ được gửi tới địa chỉ này.',
  'checkout.demoPayment': 'Thanh toán thử nghiệm',
  'checkout.demoNote': 'Không sử dụng thông tin thẻ hay tiền thật.',
  'checkout.wallet': 'Ví thử nghiệm Vietra',
  'checkout.reference': 'Mã đặt chỗ',
  'checkout.close': 'Đóng thanh toán',
  'checkout.total': 'Tổng cộng',

  'product.from': 'Từ',

  'app.tagline': 'Việt Nam, hành trình trọn vẹn',
  'app.nav.discover': 'Khám phá',
  'app.nav.curated': 'Dành riêng cho bạn',
  'app.search.destination': 'Điểm đến',
  'app.search.date': 'Ngày',
  'app.search.guests': 'Số khách',
  'app.search.explore': 'Khám phá',
  'app.search.try': 'Thử',
  'app.cta.unsure': 'Chưa biết bắt đầu từ đâu?',
  'app.cta.letMai': 'Để Mai lên lịch trình cho bạn',
  'app.filter.minRating': 'Đánh giá tối thiểu',
  'app.filter.duration': 'Thời lượng',
  'app.filter.setting': 'Không gian',
  'app.filter.terms': 'Điều kiện đặt chỗ',
  'app.filter.accessibility': 'Khả năng tiếp cận',
  'app.filter.accessibilityNote': 'Nhu cầu tiếp cận không bao giờ bị nới lỏng.',
  'app.empty.noMatch': 'Chưa có kết quả khớp chính xác',
  'app.promo.flow': 'Một ngày trọn vẹn',
  'app.promo.flowBody': 'Gợi ý được tuyển chọn, không phải chào bán ngẫu nhiên.',
  'app.promo.cook': 'Nấu, nếm, kết nối',
  'app.promo.oldTown': 'Phố cổ giờ hoàng hôn',
  'app.promo.lanterns': 'Đèn lồng & hương vị bên sông',
  'app.mai.role': 'Người bản địa đồng hành cùng bạn',
  'app.mai.budget': 'Trong ngân sách của bạn',
  'app.mai.noClash': 'Không trùng lịch',
  'app.mai.more': 'Hơn cả một chatbot',
  'app.mai.moreBody': 'Trợ lý am hiểu bản địa và đặt chỗ được thật sự.',
  'app.detail.chosen': 'Được chọn cho chuyến đi của bạn',
  'app.detail.why': 'Vì sao Mai gợi ý điều này',
  'app.detail.selectedDate': 'Ngày đã chọn',
  'app.detail.totalFrom': 'Tổng từ',
  'app.a11y.dismissError': 'Bỏ qua thông báo lỗi',
  'app.a11y.home': 'Trang chủ Vietra',
  'app.a11y.menu': 'Menu',
  'app.search.placeholder': 'Một ngày thư thái cùng gia đình với ẩm thực và văn hoá…',
  'app.a11y.visitDate': 'Ngày tham quan',
  'app.a11y.removeTraveller': 'Bớt một khách',
  'app.a11y.addTraveller': 'Thêm một khách',
  'app.filter.noLimit': 'Không giới hạn',
  'app.a11y.maxPrice': 'Giá tối đa',
  'app.a11y.dismissSuggestion': 'Bỏ qua gợi ý',
  'app.a11y.closeDetails': 'Đóng chi tiết',
  'app.a11y.closeMenu': 'Đóng menu',
  'app.cta.askMai': 'Hỏi Mai',
  'app.hero.title': 'Những trải nghiệm tinh tế, hợp với bạn',
  'app.hero.subtitle': 'Tìm nhịp điệu của riêng bạn',
  'app.search.label': 'Điều gì sẽ khiến chuyến đi này đáng nhớ?',
  'app.trust.vouchers': 'Voucher điện tử tức thì',
  'app.trust.cancellation': 'Huỷ linh hoạt',
  'app.trust.explain': 'Gợi ý luôn kèm lý do',
  'app.filter.all': 'Tất cả bộ lọc',
  'app.filter.instant': 'Xác nhận tức thì',
  'app.filter.freeCancel': 'Miễn phí huỷ',
  'app.filter.family': 'Phù hợp gia đình',
  'app.filter.clear': 'Xoá bộ lọc',
  'app.cta.askHelp': 'Nhờ Mai hỗ trợ',
  'app.cta.completeDay': 'Nhờ Mai hoàn thiện ngày này',
  'app.cta.applyPlan': 'Áp dụng lịch trình này',
  'app.cta.startPlanning': 'Bắt đầu lên kế hoạch với Mai',
  'app.cta.askAbout': 'Hỏi về trải nghiệm này',
  'app.cta.addToTrip': 'Thêm vào chuyến đi',

  'assistant.a11y.panel': 'Trợ lý mua sắm Mai',
  'assistant.title': 'Mai, người lên lịch trình bản địa của bạn',
  'assistant.ready': 'Sẵn sàng hỗ trợ',
  'assistant.a11y.close': 'Đóng trợ lý',
  'assistant.availableOnDate': 'Còn chỗ vào ngày bạn chọn',
  'assistant.placeholder': 'Hỏi về giờ giấc, lối vào, giá cả, hoặc lên một lịch trình…',
  'assistant.a11y.send': 'Gửi',
  'content.fallback': 'Hiển thị bằng tiếng Anh — chưa được dịch.',
  'content.stale': 'Mô tả này vừa được cập nhật; bản dịch đang được hoàn thiện.',

  'error.unavailable':
    'Dịch vụ Vietra hiện không khả dụng. Vui lòng thử lại sau giây lát.',
}

const dictionaries: Record<string, Dictionary> = { en, vi }

export const translate = (locale: string, key: MessageKey): string =>
  dictionaries[locale]?.[key] ?? en[key]

// Which keys a locale has not yet been given. The enablement decision is made
// against this, not against a translator's recollection.
export const missingKeys = (locale: string): MessageKey[] => {
  const dictionary = dictionaries[locale]
  if (!dictionary) return Object.keys(en) as MessageKey[]
  return (Object.keys(en) as MessageKey[]).filter((key) => !dictionary[key])
}

export const chromeCoverage = (locale: string): number => {
  const total = Object.keys(en).length
  return total === 0 ? 0 : ((total - missingKeys(locale).length) / total) * 100
}

// Surfaces that still hold literal English in JSX rather than dictionary keys.
//
// This list exists because `chromeCoverage` cannot be trusted on its own: it
// measures the keys that have been *extracted*, so a locale that translates
// every key scores 100% while the shopper reads an English home page. A
// coverage number that reports readiness the page does not deliver is the
// same failure as a translation gate that queries a status column instead of
// running the resolver. Empty this list only when the strings are gone.
export const UNEXTRACTED_SURFACES: string[] = []

// The frontend's half of the enablement gate. The backend decides which
// locales have *content*; this decides which have chrome to put it in, and a
// locale needs both. English is always ready - it is the source.
// Whether a displayed field is the language the shopper asked for. The
// backend reports `requested` alongside the served locale precisely so this
// can be answered without the frontend re-deriving the negotiation rules.
export const isFallback = (meta?: {
  locale: string
  requested?: string
}): boolean => Boolean(meta?.requested && meta.requested !== meta.locale)

export const chromeReady = (locale: string): boolean =>
  locale === 'en' ||
  (UNEXTRACTED_SURFACES.length === 0 && missingKeys(locale).length === 0)
