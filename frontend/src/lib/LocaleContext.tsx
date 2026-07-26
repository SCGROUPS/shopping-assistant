import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { translate } from './i18n'
import type { MessageKey } from './i18n'
import { LocaleContext } from './useLocale'

export const LocaleProvider = ({
  locale,
  children,
}: {
  locale: string
  children: ReactNode
}) => {
  const value = useMemo(
    () => ({ locale, t: (key: MessageKey) => translate(locale, key) }),
    [locale],
  )
  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  )
}
