// Money and date rendering.
//
// This existed five times, byte-identical, in App, ProductCard, CartDrawer,
// CheckoutModal and AssistantPanel - each pinned to 'en-US'. Five copies is
// not five bugs, it is one bug that has to be fixed five times and will be
// fixed four, which is why localising them separately was never an option.
//
// The locale argument is a *formatting* locale, unrelated to which language
// the text is in. A Vietnamese shopper paying in dollars should read
// "1.234,50 US$", not "$1,234.50": the currency is a fact about the price and
// the grouping is a fact about the reader.

const BCP47: Record<string, string> = {
  en: 'en-US',
  vi: 'vi-VN',
  zh: 'zh-CN',
  ja: 'ja-JP',
  ko: 'ko-KR',
  fr: 'fr-FR',
  de: 'de-DE',
  es: 'es-ES',
}

export const bcp47 = (locale: string) => BCP47[locale] ?? locale ?? 'en-US'

export const formatMoney = (
  locale: string,
  currency: string,
  amount: number,
) =>
  new Intl.NumberFormat(bcp47(locale), {
    style: 'currency',
    currency,
    // Dong has no subunit in practice; printing "₫1,500,000.00" reads as a
    // rounding error rather than a price.
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

export const formatCount = (locale: string, value: number) =>
  new Intl.NumberFormat(bcp47(locale)).format(value)

export const formatDate = (
  locale: string,
  value: string | Date,
  options: Intl.DateTimeFormatOptions,
) => {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(bcp47(locale), options).format(date)
}

export const formatTime = (locale: string, value: string | Date) =>
  formatDate(locale, value, { hour: '2-digit', minute: '2-digit' })
