import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { LocaleContext, buildTranslator } from './useLocale'

export const LocaleProvider = ({
  locale,
  children,
}: {
  locale: string
  children: ReactNode
}) => {
  const value = useMemo(
    () => ({ locale, t: buildTranslator(locale) }),
    [locale],
  )
  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  )
}
