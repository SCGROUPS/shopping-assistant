import { createContext, useContext } from 'react'
import { translate } from './i18n'
import type { MessageKey } from './i18n'

export type Translator = (key: MessageKey) => string

// The locale here is the one the *server resolved*, propagated down so that
// chrome and catalogue text can never disagree. If the UI translated to the
// requested locale while the API fell back to English, the page would carry
// Vietnamese buttons around English descriptions and look broken in a way no
// single component is responsible for.
export const LocaleContext = createContext<{
  locale: string
  t: Translator
}>({
  locale: 'en',
  t: (key) => translate('en', key),
})

export const useLocale = () => useContext(LocaleContext)

export const useT = (): Translator => useContext(LocaleContext).t
