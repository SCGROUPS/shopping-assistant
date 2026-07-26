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

import { bcp47, formatMoney } from './format'

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
  'badge.instant_confirmation': 'Instant confirmation',
  'badge.family_friendly': 'Family friendly',
  'badge.free_cancellation': 'Free cancellation',
  'badge.free_cancellation_hours': 'Free cancellation {hours}h',
  'badge.available': 'Available',
  'badge.sold_out': 'Sold out',
  'product.duration.minutes': '{minutes} min',
  'product.duration.hours_one': '{hours} hour',
  'product.duration.hours_other': '{hours} hours',
  'product.duration.hoursMinutes': '{hours}h {minutes}m',
  'assistant.askAbout': 'Tell me more about {title}.',
  'voice.heard': 'Heard: {transcript}',
  'voice.hearing': 'Listening: {transcript}',

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
  'app.search.voice': 'Search by voice',
  'app.search.voiceStop': 'Stop listening',
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
  'assistant.soldOut': 'Sold out for your date',
  'assistant.placeholder': 'Ask about timing, access, prices, or build a plan…',
  'assistant.a11y.send': 'Send',
  'assistant.a11y.voice': 'Talk to Mai',
  'assistant.lookingAt': 'Looking at {count} experiences in Central Vietnam',
  'assistant.thinking': 'Checking fit, timing, and availability…',
  'assistant.footnote':
    'Mai checks catalog facts and live demo availability before taking action.',

  'card.from': 'From',
  'card.perGuest': 'per guest',
  'card.perGuestWith': 'per guest · {price}',
  'card.ask': 'Ask about this',
  'card.askAbout': 'Ask Mai about {title}',
  'card.viewDetails': 'View details',
  'card.a11y.view': 'View {title}',
  'card.add': 'Add',
  'card.a11y.add': 'Add {title}',
  'card.a11y.remove': 'Remove {title}',
  'card.a11y.action': '{action} for {title}',

  'cart.clash': 'Two items clash — ask Mai to re-time one',
  'cart.checkPlan': 'Check my plan with Mai',

  'checkout.a11y.backdrop': 'Close',
  'checkout.eyebrow.confirmed': 'Booking confirmed',
  'checkout.eyebrow.secure': 'Secure demo checkout',
  'checkout.heading.confirmed': 'Your Vietnam moments are booked',
  'checkout.heading.review': 'One last check',
  'checkout.ready':
    'Everything is ready. Your mobile vouchers are grouped under one easy booking reference.',
  'checkout.collection': 'Central Vietnam collection',
  'checkout.a11y.qr': 'Booking voucher QR code',
  'checkout.a11y.qrDemo': 'Demo QR code',
  'checkout.saveVoucher': 'Save voucher',
  'checkout.keepExploring': 'Keep exploring',
  'checkout.fullName': 'Full name',
  'checkout.email': 'Email',
  'checkout.cardLine': '•••• 4242 · Always approved',
  'checkout.secureNote':
    'This is a simulated purchase for the proof of concept.',
  'checkout.confirming': 'Confirming…',
  'checkout.confirm': 'Confirm demo purchase',
  'checkout.terms':
    'By confirming, you accept the mock supplier terms and cancellation policies shown for each experience.',
  'checkout.error':
    'We could not complete the simulated booking. Recheck the cart and try again.',

  'app.hero.suffix': 'in Vietnam.',
  'app.hero.lede':
    'Search the classic way or describe the day you imagine. Mai will balance place, pace, weather, and the people you travel with.',
  'app.results.yourSearch': 'Your search',
  'app.results.discover': 'Discover Central Vietnam',
  'app.results.shaped': 'Experiences shaped around your request',
  'app.results.feeling': 'Choose the feeling, not just the ticket',
  'app.results.lede':
    'Live availability, practical details, and honest reasons each experience might fit.',
  'app.filter.budgetFor': 'Budget (total for {count})',
  'app.filter.indoor': 'Indoor',
  'app.filter.outdoor': 'Outdoor',
  'relaxed.max_duration': 'the maximum duration',
  'relaxed.rating': 'the minimum rating',
  'relaxed.instant_confirmation': 'instant confirmation',
  'relaxed.free_cancellation': 'free cancellation',
  'relaxed.category': 'the activity type',
  'relaxed.indoor_outdoor': 'the indoor or outdoor preference',
  'relaxed.language': 'the guide language',
  'relaxed.family_friendly': 'the family-friendly filter',
  'relaxed.dates': 'the exact date, searching a few days either side',
  'relaxed.budget': 'the budget',
  'relaxed.destination': 'the destination',
  'app.relaxed.prefix': 'No exact match, so we relaxed',
  'app.relaxed.suffix':
    'to keep bookable options on screen. Your accessibility needs were kept intact.',
  'app.relaxed.note':
    'Mai can relax a preference while keeping your important constraints intact.',
  // Offered, not applied. Nothing is given up until the shopper picks one.
  'app.relaxOffer.lead':
    'Nothing matches everything you asked for. Which of these could you set aside?',
  'app.relaxOffer.choice.max_duration': 'Ignore my duration limit',
  'app.relaxOffer.choice.rating': 'Ignore my rating limit',
  'app.relaxOffer.choice.instant_confirmation': 'Allow non-instant confirmation',
  'app.relaxOffer.choice.free_cancellation': 'Allow no free cancellation',
  'app.relaxOffer.choice.category': 'Allow other categories',
  'app.relaxOffer.choice.indoor_outdoor': 'Allow indoor or outdoor',
  'app.relaxOffer.choice.language': 'Allow another guide language',
  'app.relaxOffer.choice.family_friendly': 'Allow non-family experiences',
  'app.relaxOffer.choice.dates': 'Widen my dates',
  'app.relaxOffer.choice.budget': 'Ignore my budget limit',
  'app.relaxOffer.choice.destination': 'Look in other destinations',
  'app.plan.lede':
    'These experiences work together by location, pace, and time of day. Add one and Mai will reshape the rest of your plan.',
  'app.plan.sample': 'Market-to-table class · Hoi An',
  'app.empty.body':
    'Nothing in this destination is still bookable for your dates and party size. Try another day and I will rebuild the plan.',
  'app.plan.morning': 'Morning anchor',
  'app.plan.golden': 'Golden hour',
  'app.plan.finish': 'Easy finish',
  'app.assistantName': 'Mai',
  'app.plan.sampleAdvice':
    'Since you prefer a slower pace, I would keep Ba Na Hills as the only big outing and pair it with an easy river evening.',
  'app.assistant.lede':
    'Mai remembers your filters, explains trade-offs, checks the latest option and price, then turns recommendations into actions you can trust.',
  'app.assistant.compare': 'Compares the details that matter to you',
  'app.assistant.plans': 'Builds plans without time conflicts',
  'app.assistant.checkout': 'Guides you through voucher-ready checkout',
  'app.footer.note':
    'A proof-of-concept tourism marketplace. Product data, availability, payment, and vouchers are simulated.',
  'app.footer.places': 'Hoi An · Da Nang · Hue',
  'app.resume.title': 'Pick up where you left off',
  'app.resume.body': 'Need a thoughtful recommendation?',
  'app.resume.continue': 'Continue with Mai',
  'app.resume.ask': 'Ask Mai',
  'app.a11y.closeDetail': 'Close',
  'app.detail.reviews': '{count} verified guests',
  'app.detail.standardOption': 'Standard experience',
  'app.detail.flexibleStart': 'Flexible start',
  'app.detail.noReviews': 'no guest reviews yet',
  'app.detail.instant': 'Instant confirmation',
  'app.detail.forGuests': 'for {count}',
  'app.cart.viewTrip': 'View trip',

  // Counted phrases. The `_one`/`_other` split is English grammar; Vietnamese
  // supplies only `_other` and is complete without the singular.
  'app.travellers_one': '{count} traveller',
  'app.travellers_other': '{count} travellers',
  'app.guests_one': '{count} guest',
  'app.guests_other': '{count} guests',
  'app.hours_one': '{count} hour',
  'app.hours_other': '{count} hours',
  'app.experiences_one': '{count} experience',
  'app.experiences_other': '{count} experiences',
  'cart.adults_one': '{count} adult',
  'cart.adults_other': '{count} adults',
  'cart.children_one': '{count} child',
  'cart.children_other': '{count} children',

  'content.fallback': 'Shown in English — not yet translated.',
  'content.stale': 'This description was updated recently; the translation is catching up.',

  'error.unavailable':
    'The live Vietra service is unavailable. Please try again shortly.',
  'error.localeSwitch':
    'We could not switch languages just now. Please try again.',
  'error.search':
    'Search could not reach the live catalog. Your current results are unchanged.',
  'error.localeSession':
    'We could not restore your language settings. Please reload the page.',

  'product.newListing': 'Newly listed',

  'app.suggestion.evening': 'A magical Hoi An evening',
  'app.suggestion.family': 'Family day near Da Nang',
  'app.suggestion.unhurried': 'Food, culture, and no rushing',
  'app.suggestion.rainy': 'Rainy-day experiences',
  'app.date.choose': 'Choose date',
  'app.results.noMatch': 'No exact match',
  'assistant.prompt.checkPlan':
    'Check my plan: does the timing work, and is anything missing?',
  'assistant.prompt.familyQuery': 'family-friendly experiences',
  'assistant.prompt.fullDay': 'Build this into a relaxed full-day plan',
  'assistant.quick.halfDay': 'Plan a relaxed half-day',
  'assistant.quick.family': 'Best for a family?',
  'assistant.quick.rain': 'What works if it rains?',
  'assistant.quick.accessible': 'Find accessible options',
  'assistant.speech.stopLong': 'Stop reading this response',
  'assistant.speech.startLong': 'Read this response aloud',
  'assistant.speech.stop': 'Stop reading',
  'assistant.speech.start': 'Listen to response',
  'assistant.recommendedReason': 'Recommended for this trip',
  'product.save': 'Save experience',
  'product.unsave': 'Remove from saved',
  'product.option.standard': 'Standard experience',
  'product.untitled': 'Vietnam experience',
  'nudge.clash.sameTime': 'the same time',
  'voice.error.blocked':
    'Microphone access is blocked. Allow it in your browser to use voice.',
  'voice.error.noSpeech': 'No speech was detected. Try again when you are ready.',
  'voice.error.unavailable': 'Voice input is temporarily unavailable.',
  'voice.listening': 'Listening. Speak naturally.',
  'voice.alreadyActive': 'Voice input is already active.',
  'app.destination.any': 'Anywhere in Vietnam',

  'filter.category.all': 'All',
  'filter.category.culture': 'Culture',
  'filter.category.dayTrip': 'Day trip',
  'filter.category.food': 'Food',
  'filter.category.family': 'Family',
  'filter.category.water': 'Water',
  'filter.category.nature': 'Nature',
  'filter.category.wellness': 'Wellness',

  'filter.access.wheelchair': 'Wheelchair access',
  'filter.access.stepFree': 'Step-free route',
  'filter.access.audioGuide': 'Audio guide',
  'filter.access.signLanguage': 'Sign language',

  'filter.duration.120': 'Up to 2 hours',
  'filter.duration.240': 'Up to 4 hours',
  'filter.duration.600': 'Up to a full day',

  // Messages the client composes itself. Anything the assistant *service*
  // returns is already in the shopper's language and is carried as `raw`.
  'assistant.welcome':
    'Xin chào! I can turn a few preferences into a thoughtful Central Vietnam plan. I will check timing, travel fit, and availability before you book.',
  'assistant.defaultReply': 'I found a few experiences that fit.',

  // Sentences the assistant service composes itself, sent as codes because the
  // service has no dictionary. Everything counted or priced is rendered here so
  // it follows the shopper's own conventions.
  'unresolved.date_unverified':
    'I could not confirm the date you mentioned, so these results are not limited to it. Please pick your dates in the filters.',
  'unresolved.date_implausible':
    'The date I read did not look like a date you could book, so these results are not limited to it. Please pick your dates in the filters.',
  'assistant.degraded':
    'I could not reach my planning model, so I searched for what you wrote. I could not add anything to your cart, prepare a checkout or book. Please try again in a moment.',
  'assistant.msg.searchResults_one': 'I found one option that fits.',
  'assistant.msg.searchResults_other':
    'I found {count} options that fit. The first ones match your request most closely.',
  'assistant.msg.noResults':
    'Nothing is bookable even after I widened your dates and set aside the optional preferences. I kept your accessibility needs and everything you ruled out.',
  'assistant.msg.noResults.ask':
    'Would you like to change the destination or the travel dates?',
  'assistant.msg.compareNeedsTwo':
    'Please search for at least two experiences before asking me to compare.',
  'assistant.msg.comparison': 'Here is a fact-based comparison of the leading options.',
  'assistant.msg.noComplement':
    'I could not find a complementary experience that is still bookable for your dates and party.',
  'assistant.msg.noComplement.ask': 'Shall I look at nearby dates?',
  'assistant.msg.complements': 'These options complement your current choice.',
  'assistant.msg.added': 'Added {title} to your cart. The simulated total is {total}.',
  'assistant.msg.checkoutTotal_one':
    'Final simulated total for one item: {total}. No real payment will be taken. Confirm and I will create the booking and your QR voucher.',
  'assistant.msg.checkoutTotal_other':
    'Final simulated total for {count} items: {total}. No real payment will be taken. Confirm and I will create the booking and your QR voucher.',
  'assistant.msg.cannotBookYet':
    'I cannot book yet. Ask me to prepare the checkout first so you can review the total.',
  'assistant.msg.booked':
    'Your simulated booking {booking} is confirmed. Voucher {voucher} is ready.',
  'assistant.msg.unavailable':
    'I could not put together a reply I can stand behind, so I have not shown it. Nothing was booked. Please ask me again.',
  'assistant.reserveFailed':
    'I could not reserve {title} because its availability changed. Please choose another time or experience.',
  'assistant.addedToTrip':
    '{title} is in your trip. I rechecked the selected option and price. You can review the complete booking without leaving our conversation.',
  'assistant.catalogUnreachable':
    'I could not reach the live catalog just now. Your cart is unchanged, so please try that request again.',
  'assistant.removeFailed':
    'I could not remove {title}. Refresh the cart and try again.',
  'assistant.booked':
    'Booked! Your reference is {reference}. I kept all vouchers together so they are easy to find on the day.',

  'assistant.action.familyFavourites': 'Show family favourites',
  'assistant.action.reviewPurchase': 'Review and purchase',
  'assistant.action.addToCart': 'Add to trip',
  'assistant.action.checkAvailability': 'Check times',
  'assistant.action.prepareCheckout': 'Review checkout',
  'assistant.action.confirmSimulated': 'Confirm demo purchase',
  'assistant.action.viewVoucher': 'View voucher',
  'assistant.action.continue': 'Continue',
  'assistant.action.addNamed': 'Add {title}',
  'assistant.action.reserveNamed': 'Reserve {title}',
  'assistant.searchMatched':
    'I translated “{query}” into a few practical preferences. These have the strongest overall fit; I can compare them or shape them into a half-day plan.{relaxed}',
  'assistant.searchNoMatch':
    'I could not find a live match for “{query}”. Try relaxing the destination, date, or activity preferences and I will search again.',
  'assistant.relaxedSuffix':
    ' I relaxed {list} to keep these bookable.',

  'nudge.checkout.label': 'Questions before you book?',
  'nudge.checkout.opener':
    'You are at checkout. Ask me anything about cancellation, meeting points, or what to bring before you confirm.',
  'nudge.clash.label': 'These two clash at {time} — want me to re-time one?',
  'nudge.clash.opener':
    '“{first}” and “{second}” overlap at {time}. I can move one to a later slot or another day.',
  'nudge.clash.prompt': '{first} and {second} overlap. Can you re-time one?',
  'nudge.zeroResults.label': 'Nothing matched — want me to widen the dates?',
  'nudge.zeroResults.opener':
    'Nothing was bookable with those constraints. I can widen the dates or drop the least important preference — your accessibility needs stay untouched.',
  'nudge.zeroResults.prompt':
    'Nothing matched. Can you widen my dates and try again?',
  'nudge.comparison.label': 'Want me to compare these?',
  'nudge.comparison.opener':
    'You have looked at a few of these. I can compare them on price, timing, and what the day actually feels like.',
  'nudge.comparison.prompt':
    'Compare the experiences I have been looking at.',
  'nudge.refinement.label': 'Narrowing this down? I can help.',
  'nudge.refinement.opener':
    'You have refined this a few times. Tell me what the day should feel like and I will do the narrowing for you.',
} as const

export type MessageKey = keyof typeof en

// Partial by design: a locale may be enabled before its chrome is fully
// written, and the untranslated keys then show in English - visibly incomplete
// rather than blank. `missingKeys` below is what turns that from a silent
// state into a reportable one, so the enablement gate can refuse a locale
// whose chrome is not finished.
// The CLDR categories, not English's two. Deriving the whole key set from `en`
// meant a dictionary could only ever hold `_one` and `_other`, so French
// `_many` or Polish `_few` would be a type error in precisely the language
// that needs them - the dictionary would be structurally unable to store the
// correct translation. The *base* set still comes from English, so a key that
// exists nowhere in the source is still rejected.
type PluralCategory = 'zero' | 'one' | 'two' | 'few' | 'many' | 'other'
type PluralKey = `${PluralBase}_${PluralCategory}`
type DictionaryKey = MessageKey | PluralKey

type Dictionary = Partial<Record<DictionaryKey, string>>

const vi: Dictionary = {
  'badge.instant_confirmation': 'Xác nhận tức thì',
  'badge.family_friendly': 'Phù hợp gia đình',
  'badge.free_cancellation': 'Miễn phí hủy',
  'badge.free_cancellation_hours': 'Miễn phí hủy trước {hours} giờ',
  'badge.available': 'Còn chỗ',
  'badge.sold_out': 'Hết chỗ',
  'product.duration.minutes': '{minutes} phút',
  'product.duration.hours_other': '{hours} giờ',
  'product.duration.hoursMinutes': '{hours} giờ {minutes} phút',
  'assistant.askAbout': 'Cho tôi biết thêm về {title}.',
  'voice.heard': 'Đã nghe: {transcript}',
  'voice.hearing': 'Đang nghe: {transcript}',
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
  'app.search.voice': 'Tìm kiếm bằng giọng nói',
  'app.search.voiceStop': 'Dừng nghe',
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
  'assistant.soldOut': 'Hết chỗ vào ngày bạn chọn',
  'assistant.placeholder': 'Hỏi về giờ giấc, lối vào, giá cả, hoặc lên một lịch trình…',
  'assistant.a11y.send': 'Gửi',
  'assistant.a11y.voice': 'Nói chuyện với Mai',
  'assistant.lookingAt': 'Đang xem {count} trải nghiệm tại miền Trung Việt Nam',
  'assistant.thinking': 'Đang kiểm tra mức phù hợp, giờ giấc và chỗ trống…',
  'assistant.footnote':
    'Mai kiểm tra dữ liệu danh mục và tình trạng chỗ trống trước khi thực hiện bất kỳ thao tác nào.',

  'card.from': 'Chỉ từ',
  'card.perGuest': 'mỗi khách',
  'card.perGuestWith': 'mỗi khách · {price}',
  'card.ask': 'Hỏi về trải nghiệm này',
  'card.askAbout': 'Hỏi Mai về {title}',
  'card.viewDetails': 'Xem chi tiết',
  'card.a11y.view': 'Xem {title}',
  'card.add': 'Thêm',
  'card.a11y.add': 'Thêm {title}',
  'card.a11y.remove': 'Xoá {title}',
  'card.a11y.action': '{action} cho {title}',

  'cart.clash': 'Hai trải nghiệm bị trùng giờ — nhờ Mai sắp xếp lại',
  'cart.checkPlan': 'Nhờ Mai kiểm tra lịch trình',

  'checkout.a11y.backdrop': 'Đóng',
  'checkout.eyebrow.confirmed': 'Đã xác nhận đặt chỗ',
  'checkout.eyebrow.secure': 'Thanh toán thử nghiệm an toàn',
  'checkout.heading.confirmed': 'Hành trình Việt Nam của bạn đã được đặt',
  'checkout.heading.review': 'Kiểm tra lần cuối',
  'checkout.ready':
    'Mọi thứ đã sẵn sàng. Voucher điện tử của bạn được gom chung trong một mã đặt chỗ duy nhất.',
  'checkout.collection': 'Bộ sưu tập miền Trung',
  'checkout.a11y.qr': 'Mã QR voucher đặt chỗ',
  'checkout.a11y.qrDemo': 'Mã QR minh hoạ',
  'checkout.saveVoucher': 'Lưu voucher',
  'checkout.keepExploring': 'Tiếp tục khám phá',
  'checkout.fullName': 'Họ và tên',
  'checkout.email': 'Email',
  'checkout.cardLine': '•••• 4242 · Luôn được chấp nhận',
  'checkout.secureNote':
    'Đây là giao dịch mô phỏng phục vụ bản thử nghiệm.',
  'checkout.confirming': 'Đang xác nhận…',
  'checkout.confirm': 'Xác nhận đặt chỗ thử nghiệm',
  'checkout.terms':
    'Khi xác nhận, bạn đồng ý với điều khoản và chính sách huỷ của nhà cung cấp mô phỏng.',
  'checkout.error':
    'Chúng tôi không thể hoàn tất đặt chỗ mô phỏng. Vui lòng kiểm tra lại giỏ hàng và thử lần nữa.',

  'app.hero.suffix': 'tại Việt Nam.',
  'app.hero.lede':
    'Tìm kiếm theo cách quen thuộc hoặc mô tả ngày bạn mong muốn. Mai sẽ biến cả hai thành một lịch trình đặt được ngay.',
  'app.results.yourSearch': 'Tìm kiếm của bạn',
  'app.results.discover': 'Khám phá miền Trung Việt Nam',
  'app.results.shaped': 'Những trải nghiệm được chọn theo yêu cầu của bạn',
  'app.results.feeling': 'Chọn cảm xúc, không chỉ là tấm vé',
  'app.results.lede':
    'Chỗ trống theo thời gian thực, thông tin thiết thực và lý do trung thực cho từng lựa chọn.',
  'app.filter.budgetFor': 'Ngân sách (tổng cho {count})',
  'app.filter.indoor': 'Trong nhà',
  'app.filter.outdoor': 'Ngoài trời',
  'relaxed.max_duration': 'thời lượng tối đa',
  'relaxed.rating': 'mức đánh giá tối thiểu',
  'relaxed.instant_confirmation': 'xác nhận tức thì',
  'relaxed.free_cancellation': 'huỷ miễn phí',
  'relaxed.category': 'loại hoạt động',
  'relaxed.indoor_outdoor': 'lựa chọn trong nhà hay ngoài trời',
  'relaxed.language': 'ngôn ngữ hướng dẫn',
  'relaxed.family_friendly': 'bộ lọc phù hợp gia đình',
  'relaxed.dates': 'ngày chính xác, tìm thêm vài ngày lân cận',
  'relaxed.budget': 'ngân sách',
  'relaxed.destination': 'điểm đến',
  'app.relaxed.prefix': 'Không có kết quả khớp hoàn toàn, nên chúng tôi đã nới lỏng',
  'app.relaxed.suffix':
    'để giữ lại những lựa chọn còn đặt được. Các nhu cầu hỗ trợ tiếp cận của bạn không bao giờ bị nới lỏng.',
  'app.relaxed.note':
    'Mai có thể nới lỏng một tiêu chí mà vẫn giữ nguyên những điều kiện quan trọng với bạn.',
  'app.relaxOffer.lead':
    'Không có lựa chọn nào khớp với tất cả yêu cầu của bạn. Bạn có thể bỏ bớt tiêu chí nào?',
  'app.relaxOffer.choice.max_duration': 'Bỏ giới hạn thời lượng',
  'app.relaxOffer.choice.rating': 'Bỏ giới hạn đánh giá',
  'app.relaxOffer.choice.instant_confirmation': 'Không cần xác nhận tức thì',
  'app.relaxOffer.choice.free_cancellation': 'Không cần hủy miễn phí',
  'app.relaxOffer.choice.category': 'Xem thêm danh mục khác',
  'app.relaxOffer.choice.indoor_outdoor': 'Trong nhà hay ngoài trời đều được',
  'app.relaxOffer.choice.language': 'Chấp nhận ngôn ngữ hướng dẫn khác',
  'app.relaxOffer.choice.family_friendly': 'Không cần phù hợp gia đình',
  'app.relaxOffer.choice.dates': 'Mở rộng ngày đi',
  'app.relaxOffer.choice.budget': 'Bỏ giới hạn ngân sách',
  'app.relaxOffer.choice.destination': 'Tìm ở điểm đến khác',
  'app.plan.lede':
    'Những trải nghiệm này kết hợp tốt với nhau về địa điểm, nhịp độ và thời điểm trong ngày.',
  'app.plan.sample': 'Lớp nấu ăn từ chợ đến bàn · Hội An',
  'app.empty.body':
    'Không còn trải nghiệm nào đặt được tại điểm đến này cho ngày bạn chọn. Mai có thể gợi ý những lựa chọn gần nhất.',
  'app.plan.morning': 'Điểm nhấn buổi sáng',
  'app.plan.golden': 'Giờ hoàng hôn',
  'app.plan.finish': 'Kết ngày nhẹ nhàng',
  'app.assistantName': 'Mai',
  'app.plan.sampleAdvice':
    'Vì bạn thích nhịp độ thong thả, tôi sẽ giữ Bà Nà Hills trọn buổi sáng và ghép với một buổi tối thư giãn.',
  'app.assistant.lede':
    'Mai ghi nhớ bộ lọc của bạn, giải thích các đánh đổi, kiểm tra lịch và không bao giờ bịa ra trải nghiệm.',
  'app.assistant.compare': 'So sánh những chi tiết quan trọng với bạn',
  'app.assistant.plans': 'Lên lịch trình không bị trùng giờ',
  'app.assistant.checkout': 'Hướng dẫn bạn thanh toán và nhận voucher',
  'app.footer.note':
    'Đây là sàn du lịch bản thử nghiệm. Dữ liệu sản phẩm, chỗ trống và thanh toán đều được mô phỏng.',
  'app.footer.places': 'Hội An · Đà Nẵng · Huế',
  'app.resume.title': 'Tiếp tục từ nơi bạn dừng lại',
  'app.resume.body': 'Cần một gợi ý được cân nhắc kỹ?',
  'app.resume.continue': 'Tiếp tục với Mai',
  'app.resume.ask': 'Hỏi Mai',
  'app.a11y.closeDetail': 'Đóng',
  'app.detail.reviews': '{count} khách đã xác thực',
  'app.detail.standardOption': 'Trải nghiệm tiêu chuẩn',
  'app.detail.flexibleStart': 'Giờ khởi hành linh hoạt',
  'app.detail.noReviews': 'chưa có đánh giá từ khách',
  'app.detail.instant': 'Xác nhận tức thì',
  'app.detail.forGuests': 'cho {count}',
  'app.cart.viewTrip': 'Xem chuyến đi',

  // Tiếng Việt không biến đổi theo số, nên chỉ cần dạng `_other`.
  'app.travellers_other': '{count} khách',
  'app.guests_other': '{count} khách',
  'app.hours_other': '{count} giờ',
  'app.experiences_other': '{count} trải nghiệm',
  'cart.adults_other': '{count} người lớn',
  'cart.children_other': '{count} trẻ em',
  'content.fallback': 'Hiển thị bằng tiếng Anh — chưa được dịch.',
  'content.stale': 'Mô tả này vừa được cập nhật; bản dịch đang được hoàn thiện.',

  'error.unavailable':
    'Dịch vụ Vietra hiện không khả dụng. Vui lòng thử lại sau giây lát.',
  'error.localeSwitch':
    'Chúng tôi chưa thể chuyển ngôn ngữ lúc này. Vui lòng thử lại.',
  'error.search':
    'Không thể kết nối tới kho trải nghiệm. Kết quả hiện tại của bạn không thay đổi.',
  'error.localeSession':
    'Chúng tôi không khôi phục được cài đặt ngôn ngữ. Vui lòng tải lại trang.',

  'product.newListing': 'Mới đăng',

  'app.suggestion.evening': 'Một buổi tối Hội An lung linh',
  'app.suggestion.family': 'Ngày dành cho gia đình gần Đà Nẵng',
  'app.suggestion.unhurried': 'Ẩm thực, văn hoá và không vội vã',
  'app.suggestion.rainy': 'Trải nghiệm cho ngày mưa',
  'app.date.choose': 'Chọn ngày',
  'app.results.noMatch': 'Chưa khớp chính xác',
  'assistant.prompt.checkPlan':
    'Kiểm tra lịch trình giúp tôi: thời gian có hợp lý không, và còn thiếu gì không?',
  'assistant.prompt.familyQuery': 'trải nghiệm phù hợp cho gia đình',
  'assistant.prompt.fullDay': 'Sắp xếp thành một ngày trọn vẹn và thong thả',
  'assistant.quick.halfDay': 'Lên lịch nửa ngày thong thả',
  'assistant.quick.family': 'Lựa chọn nào hợp với gia đình?',
  'assistant.quick.rain': 'Trời mưa thì đi đâu?',
  'assistant.quick.accessible': 'Tìm lựa chọn dễ tiếp cận',
  'assistant.speech.stopLong': 'Dừng đọc câu trả lời này',
  'assistant.speech.startLong': 'Đọc to câu trả lời này',
  'assistant.speech.stop': 'Dừng đọc',
  'assistant.speech.start': 'Nghe câu trả lời',
  'assistant.recommendedReason': 'Gợi ý cho chuyến đi này',
  'product.save': 'Lưu trải nghiệm',
  'product.unsave': 'Bỏ khỏi mục đã lưu',
  'product.option.standard': 'Trải nghiệm tiêu chuẩn',
  'product.untitled': 'Trải nghiệm Việt Nam',
  'nudge.clash.sameTime': 'cùng một khung giờ',
  'voice.error.blocked':
    'Micro đang bị chặn. Hãy cho phép truy cập trong trình duyệt để dùng giọng nói.',
  'voice.error.noSpeech': 'Không nhận được giọng nói. Hãy thử lại khi bạn sẵn sàng.',
  'voice.error.unavailable': 'Nhập bằng giọng nói tạm thời không khả dụng.',
  'voice.listening': 'Đang nghe. Bạn cứ nói tự nhiên.',
  'voice.alreadyActive': 'Nhập bằng giọng nói đang hoạt động.',
  'app.destination.any': 'Khắp Việt Nam',

  'filter.category.all': 'Tất cả',
  'filter.category.culture': 'Văn hoá',
  'filter.category.dayTrip': 'Đi trong ngày',
  'filter.category.food': 'Ẩm thực',
  'filter.category.family': 'Gia đình',
  'filter.category.water': 'Sông nước',
  'filter.category.nature': 'Thiên nhiên',
  'filter.category.wellness': 'Nghỉ dưỡng',

  'filter.access.wheelchair': 'Lối đi cho xe lăn',
  'filter.access.stepFree': 'Lối đi không bậc thang',
  'filter.access.audioGuide': 'Thuyết minh bằng âm thanh',
  'filter.access.signLanguage': 'Ngôn ngữ ký hiệu',

  'filter.duration.120': 'Tối đa 2 giờ',
  'filter.duration.240': 'Tối đa 4 giờ',
  'filter.duration.600': 'Tối đa một ngày',

  'assistant.welcome':
    'Xin chào! Chỉ với một vài sở thích, tôi có thể dựng nên một hành trình miền Trung chu đáo. Tôi sẽ kiểm tra thời gian, mức độ phù hợp và tình trạng chỗ trước khi bạn đặt.',
  'assistant.defaultReply': 'Tôi tìm được một vài trải nghiệm phù hợp.',

  'unresolved.date_unverified':
    'Tôi chưa xác nhận được ngày bạn nhắc tới, nên kết quả này không giới hạn theo ngày đó. Vui lòng chọn ngày trong bộ lọc.',
  'unresolved.date_implausible':
    'Ngày tôi đọc được không giống một ngày có thể đặt, nên kết quả này không giới hạn theo ngày đó. Vui lòng chọn ngày trong bộ lọc.',
  'assistant.degraded':
    'Tôi chưa kết nối được tới mô hình lập kế hoạch, nên tôi chỉ tìm theo đúng nội dung bạn viết. Tôi chưa thể thêm vào giỏ, chuẩn bị thanh toán hay đặt chỗ. Vui lòng thử lại sau giây lát.',
  'assistant.msg.searchResults_other':
    'Tôi tìm được {count} lựa chọn phù hợp. Những lựa chọn đầu tiên sát với yêu cầu của bạn nhất.',
  'assistant.msg.noResults':
    'Không còn chỗ nào có thể đặt, ngay cả khi tôi đã nới rộng ngày và tạm bỏ các sở thích không bắt buộc. Tôi vẫn giữ nguyên nhu cầu tiếp cận và những điều bạn muốn loại trừ.',
  'assistant.msg.noResults.ask': 'Bạn có muốn đổi điểm đến hoặc ngày đi không?',
  'assistant.msg.compareNeedsTwo':
    'Vui lòng tìm ít nhất hai trải nghiệm trước khi nhờ tôi so sánh.',
  'assistant.msg.comparison':
    'Đây là bảng so sánh dựa trên dữ liệu thực tế của những lựa chọn nổi bật.',
  'assistant.msg.noComplement':
    'Tôi chưa tìm được trải nghiệm bổ sung nào còn chỗ cho ngày và số khách của bạn.',
  'assistant.msg.noComplement.ask': 'Tôi tìm thử những ngày gần đó nhé?',
  'assistant.msg.complements': 'Những lựa chọn này bổ sung tốt cho trải nghiệm bạn đang chọn.',
  'assistant.msg.added': 'Đã thêm {title} vào giỏ. Tổng tiền mô phỏng là {total}.',
  'assistant.msg.checkoutTotal_other':
    'Tổng tiền mô phỏng cho {count} mục: {total}. Sẽ không có khoản thanh toán thật nào. Bạn xác nhận thì tôi sẽ tạo đơn đặt và phiếu QR.',
  'assistant.msg.cannotBookYet':
    'Tôi chưa thể đặt chỗ. Hãy yêu cầu tôi chuẩn bị thanh toán trước để bạn xem lại tổng tiền.',
  'assistant.msg.booked':
    'Đơn đặt mô phỏng {booking} của bạn đã được xác nhận. Phiếu {voucher} đã sẵn sàng.',
  'assistant.msg.unavailable':
    'Tôi chưa soạn được câu trả lời đủ tin cậy nên không hiển thị. Không có đơn nào được đặt. Vui lòng hỏi lại giúp tôi.',
  'assistant.reserveFailed':
    'Tôi không thể giữ chỗ {title} vì tình trạng chỗ đã thay đổi. Vui lòng chọn khung giờ hoặc trải nghiệm khác.',
  'assistant.addedToTrip':
    'Đã thêm {title} vào hành trình của bạn. Tôi đã kiểm tra lại lựa chọn và giá. Bạn có thể xem toàn bộ đơn đặt ngay trong cuộc trò chuyện này.',
  'assistant.catalogUnreachable':
    'Tôi chưa kết nối được tới kho trải nghiệm. Giỏ hàng của bạn không thay đổi, vui lòng thử lại yêu cầu đó.',
  'assistant.removeFailed':
    'Tôi không thể xoá {title}. Hãy tải lại giỏ hàng và thử lại.',
  'assistant.booked':
    'Đã đặt xong! Mã đặt chỗ của bạn là {reference}. Tôi đã gom tất cả voucher lại một chỗ để bạn dễ tìm trong ngày đi.',

  'assistant.action.familyFavourites': 'Xem lựa chọn cho gia đình',
  'assistant.action.reviewPurchase': 'Xem lại và thanh toán',
  'assistant.action.addToCart': 'Thêm vào hành trình',
  'assistant.action.checkAvailability': 'Xem khung giờ',
  'assistant.action.prepareCheckout': 'Xem lại thanh toán',
  'assistant.action.confirmSimulated': 'Xác nhận đặt thử',
  'assistant.action.viewVoucher': 'Xem voucher',
  'assistant.action.continue': 'Tiếp tục',
  'assistant.action.addNamed': 'Thêm {title}',
  'assistant.action.reserveNamed': 'Giữ chỗ {title}',
  'assistant.searchMatched':
    'Tôi đã chuyển “{query}” thành một vài tiêu chí cụ thể. Đây là những lựa chọn phù hợp nhất; tôi có thể so sánh chúng hoặc sắp thành một lịch trình nửa ngày.{relaxed}',
  'assistant.searchNoMatch':
    'Tôi không tìm được lựa chọn nào còn chỗ cho “{query}”. Hãy thử nới lỏng điểm đến, ngày hoặc loại hoạt động, tôi sẽ tìm lại.',
  'assistant.relaxedSuffix':
    ' Tôi đã nới lỏng {list} để giữ được chỗ đặt.',

  'nudge.checkout.label': 'Bạn có câu hỏi nào trước khi đặt không?',
  'nudge.checkout.opener':
    'Bạn đang ở bước thanh toán. Hãy hỏi tôi bất cứ điều gì về chính sách huỷ, điểm hẹn hoặc những thứ cần mang theo trước khi xác nhận.',
  'nudge.clash.label': 'Hai hoạt động này trùng giờ lúc {time} — tôi đổi giờ một cái nhé?',
  'nudge.clash.opener':
    '“{first}” và “{second}” trùng nhau lúc {time}. Tôi có thể dời một hoạt động sang khung giờ muộn hơn hoặc sang ngày khác.',
  'nudge.clash.prompt':
    '{first} và {second} bị trùng giờ. Bạn đổi giúp tôi một cái được không?',
  'nudge.zeroResults.label':
    'Không có kết quả nào — tôi mở rộng khoảng ngày nhé?',
  'nudge.zeroResults.opener':
    'Không có lựa chọn nào còn chỗ với các điều kiện đó. Tôi có thể mở rộng khoảng ngày hoặc bỏ bớt tiêu chí ít quan trọng nhất — các nhu cầu hỗ trợ tiếp cận của bạn vẫn được giữ nguyên.',
  'nudge.zeroResults.prompt':
    'Không có kết quả nào. Bạn mở rộng khoảng ngày rồi thử lại giúp tôi nhé?',
  'nudge.comparison.label': 'Bạn muốn tôi so sánh những lựa chọn này không?',
  'nudge.comparison.opener':
    'Bạn đã xem qua một vài lựa chọn. Tôi có thể so sánh chúng theo giá, thời gian và cảm nhận thực tế của cả ngày.',
  'nudge.comparison.prompt':
    'Hãy so sánh những trải nghiệm tôi vừa xem.',
  'nudge.refinement.label': 'Bạn đang thu hẹp lựa chọn? Để tôi giúp nhé.',
  'nudge.refinement.opener':
    'Bạn đã lọc lại vài lần rồi. Hãy nói cho tôi biết bạn muốn ngày hôm đó như thế nào, tôi sẽ thu hẹp giúp bạn.',
}

const dictionaries: Record<string, Dictionary> = { en, vi }

/**
 * A money amount that has not been formatted yet, because it cannot be until
 * the locale is known. The assistant service sends the number and the currency;
 * where the grouping separators go, and whether the symbol leads or trails, is
 * the shopper's convention and not the server's.
 */
export type MoneyVar = { amount: number; currency: string }

export type MessageVars = Record<string, string | number | MoneyVar>

const isMoney = (value: unknown): value is MoneyVar =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as MoneyVar).amount === 'number' &&
  typeof (value as MoneyVar).currency === 'string'

const PLURAL_KEY = /^(.*)_(zero|one|two|few|many|other)$/

// Which plural categories a language actually uses. English needs one/other,
// Vietnamese needs only other, Polish needs one/few/many/other. Asking every
// language for English's categories would mark Vietnamese incomplete forever;
// asking for its own makes the readiness gate mean something.
const pluralCategories = (locale: string): string[] => {
  try {
    return new Intl.PluralRules(bcp47(locale)).resolvedOptions().pluralCategories
  } catch {
    return ['other']
  }
}

const interpolate = (
  locale: string,
  template: string,
  vars?: MessageVars,
): string =>
  vars
    ? template.replace(/\{(\w+)\}/g, (whole, name: string) => {
        if (!(name in vars)) return whole
        const value = vars[name]
        return isMoney(value)
          ? formatMoney(locale, value.currency, value.amount)
          : String(value)
      })
    : template

export const translate = (
  locale: string,
  key: MessageKey,
  vars?: MessageVars,
): string => interpolate(locale, dictionaries[locale]?.[key] ?? en[key], vars)

/**
 * Text that has not been rendered yet, and therefore still has a language.
 *
 * The i18n build check reads TSX and can only see a string where it is written.
 * It cannot follow one stored in an object, returned from a `.ts` module, or
 * put into state and rendered three files away - which is how every nudge,
 * error and assistant line escaped it. So the *type* carries the obligation
 * instead: a field of this type accepts a dictionary key, or `raw` text that
 * the writer is asserting is already in the shopper's language.
 *
 * `raw` exists because the assistant service answers in the shopper's language
 * and that prose has no key. The build check flags a `raw` built from a string
 * literal, so it cannot be used to smuggle English past the gate.
 */
export type LocalizedText =
  | { key: MessageKey; vars?: MessageVars }
  // A counted sentence. The category has to be chosen against the locale, which
  // is only known at render, so the count travels with the text rather than
  // being resolved where the message is built.
  | { plural: PluralBase; count: number; vars?: MessageVars }
  | { raw: string }

export const resolveText = (locale: string, text: LocalizedText): string => {
  if ('raw' in text) return text.raw
  if ('plural' in text)
    return translatePlural(locale, text.plural, text.count, text.vars)
  return translate(locale, text.key, text.vars)
}

// The bases of the counted messages, derived from the `_other` form every
// language has. Typed from the dictionary so a mistyped base is a build error.
export type PluralBase = {
  [K in MessageKey]: K extends `${infer B}_other` ? B : never
}[MessageKey]

// A counted phrase, chosen by the language's own rules rather than by
// appending "s". Vietnamese does not inflect for number, so a `{count}
// traveller(s)` built by concatenation in English is untranslatable there:
// the dictionary has to own the whole phrase.
export const translatePlural = (
  locale: string,
  base: PluralBase,
  count: number,
  vars?: MessageVars,
): string => {
  const category = pluralCategories(locale).includes('other')
    ? new Intl.PluralRules(bcp47(locale)).select(count)
    : 'other'
  const dictionary: Dictionary = dictionaries[locale] ?? en
  const key = `${base}_${category}` as DictionaryKey
  const template =
    dictionary[key] ??
    dictionary[`${base}_other` as DictionaryKey] ??
    // English cannot supply a category it does not have, so the fallback for a
    // missing `_many` is English's `_other`, not a lookup that returns nothing.
    en[key as MessageKey] ??
    en[`${base}_other` as MessageKey]
  return interpolate(locale, template ?? base, { count, ...vars })
}

// Which keys a locale has not yet been given. The enablement decision is made
// against this, not against a translator's recollection.
export const missingKeys = (locale: string): string[] => {
  const dictionary = dictionaries[locale]
  if (!dictionary) return Object.keys(en)

  const missing: string[] = []
  const bases = new Set<string>()
  for (const key of Object.keys(en) as MessageKey[]) {
    const match = PLURAL_KEY.exec(key)
    if (match) {
      bases.add(match[1])
      continue
    }
    if (!dictionary[key]) missing.push(key)
  }
  for (const base of bases) {
    for (const category of pluralCategories(locale)) {
      const key = `${base}_${category}` as DictionaryKey
      if (!dictionary[key]) missing.push(key)
    }
  }
  return missing
}

export const chromeCoverage = (locale: string): number => {
  const total = Object.keys(en).length
  return total === 0 ? 0 : ((total - missingKeys(locale).length) / total) * 100
}

// The frontend's half of the enablement gate. The backend decides which
// locales have *content*; this decides which have chrome to put it in, and a
// locale needs both. English is always ready - it is the source.
//
// There was a hand-maintained list of "surfaces still holding literal English"
// here. It was wrong the day it was written: it claimed to be empty while the
// checkout was entirely English, so `chromeCoverage` reported 100% for a page
// no Vietnamese speaker could use. `npm run check:i18n` now walks the syntax
// tree for shopper-facing literals and fails the build, which is the same
// claim made by something that cannot forget.
export const chromeReady = (locale: string): boolean =>
  locale === 'en' || missingKeys(locale).length === 0

// Whether a displayed field is the language the shopper asked for. Read from
// the backend's own `fallback` flag rather than re-derived here: negotiation
// has one owner, and a second implementation of it will disagree eventually.
export const isFallback = (meta?: { fallback?: boolean }): boolean =>
  meta?.fallback === true
