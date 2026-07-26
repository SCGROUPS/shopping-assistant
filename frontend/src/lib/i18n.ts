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

import { bcp47 } from './format'

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
  'app.relaxed.prefix': 'No exact match, so we relaxed',
  'app.relaxed.suffix':
    'to keep bookable options on screen. Your accessibility needs were kept intact.',
  'app.relaxed.note':
    'Mai can relax a preference while keeping your important constraints intact.',
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
  'app.relaxed.prefix': 'Không có kết quả khớp hoàn toàn, nên chúng tôi đã nới lỏng',
  'app.relaxed.suffix':
    'để giữ lại những lựa chọn còn đặt được. Các nhu cầu hỗ trợ tiếp cận của bạn không bao giờ bị nới lỏng.',
  'app.relaxed.note':
    'Mai có thể nới lỏng một tiêu chí mà vẫn giữ nguyên những điều kiện quan trọng với bạn.',
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
}

const dictionaries: Record<string, Dictionary> = { en, vi }

export type MessageVars = Record<string, string | number>

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

const interpolate = (template: string, vars?: MessageVars): string =>
  vars
    ? template.replace(/\{(\w+)\}/g, (whole, name: string) =>
        name in vars ? String(vars[name]) : whole,
      )
    : template

export const translate = (
  locale: string,
  key: MessageKey,
  vars?: MessageVars,
): string => interpolate(dictionaries[locale]?.[key] ?? en[key], vars)

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
  const dictionary = dictionaries[locale] ?? en
  const key = `${base}_${category}` as MessageKey
  const template =
    dictionary[key] ??
    dictionary[`${base}_other` as MessageKey] ??
    en[key] ??
    en[`${base}_other` as MessageKey]
  return interpolate(template ?? base, { count, ...vars })
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
      const key = `${base}_${category}` as MessageKey
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
