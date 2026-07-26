import { createContext, useContext } from 'react'
import { translate, translatePlural } from './i18n'
import type { MessageKey, MessageVars, PluralBase } from './i18n'

// `t('key')` for a fixed phrase, `t('key', { name })` when the sentence has a
// hole in it, and `t.plural('base', n)` when the phrase changes with a count.
// The last one exists because "{n} traveller" + "s" is an English grammar rule
// hardcoded into the markup: Vietnamese does not inflect for number and other
// languages have up to six forms, so the dictionary has to own the phrase.
export type Translator = {
  (key: MessageKey, vars?: MessageVars): string
  plural: (base: PluralBase, count: number, vars?: MessageVars) => string
}

export const buildTranslator = (locale: string): Translator => {
  const t = ((key: MessageKey, vars?: MessageVars) =>
    translate(locale, key, vars)) as Translator
  t.plural = (base, count, vars) => translatePlural(locale, base, count, vars)
  return t
}

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
  t: buildTranslator('en'),
})

export const useLocale = () => useContext(LocaleContext)

export const useT = (): Translator => useContext(LocaleContext).t
