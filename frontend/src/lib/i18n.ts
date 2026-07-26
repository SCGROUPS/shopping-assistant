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
