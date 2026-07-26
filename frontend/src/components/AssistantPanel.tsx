import {
  ArrowRight,
  Bot,
  Check,
  LoaderCircle,
  MessageCircle,
  Send,
  ShoppingBag,
  Sparkles,
  Star,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type {
  AssistantAction,
  AssistantMessage,
  Experience,
} from '../types'
import { hasReviews } from '../lib/rating'
import { VoiceInputButton } from './VoiceInputButton'
import { useLocale, useT } from '../lib/useLocale'
import { badgeLabels } from '../lib/badges'
import { bcp47, formatMoney } from '../lib/format'
import { resolveText } from '../lib/i18n'

type AssistantPanelProps = {
  open: boolean
  messages: AssistantMessage[]
  busy: boolean
  visibleProducts: Experience[]
  onClose: () => void
  onSend: (message: string) => void
  onAction: (action: AssistantAction, products?: Experience[]) => void
  onView: (product: Experience) => void
}

const promptKeys = [
  'assistant.quick.halfDay',
  'assistant.quick.family',
  'assistant.quick.rain',
  'assistant.quick.accessible',
] as const

export function AssistantPanel({
  open,
  messages,
  busy,
  visibleProducts,
  onClose,
  onSend,
  onAction,
  onView,
}: AssistantPanelProps) {
  // Formatting locale comes from the provider, not a module constant, so
  // prices re-render when the shopper switches language.
  const { locale } = useLocale()
  const t = useT()
  const money = (currency: string, amount: number) =>
    formatMoney(locale, currency, amount)
  const [value, setValue] = useState('')
  const [speakingMessageId, setSpeakingMessageId] = useState<string>()
  const endRef = useRef<HTMLDivElement>(null)
  const canSpeak =
    typeof window !== 'undefined' &&
    'speechSynthesis' in window &&
    'SpeechSynthesisUtterance' in window

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  useEffect(() => {
    if (!open && canSpeak) {
      window.speechSynthesis.cancel()
      setSpeakingMessageId(undefined)
    }
  }, [canSpeak, open])

  useEffect(
    () => () => {
      if (canSpeak) window.speechSynthesis.cancel()
    },
    [canSpeak],
  )

  const submit = () => {
    const text = value.trim()
    if (!text || busy) return
    onSend(text)
    setValue('')
  }

  const toggleSpeech = (message: AssistantMessage) => {
    if (!canSpeak) return
    if (speakingMessageId === message.id) {
      window.speechSynthesis.cancel()
      setSpeakingMessageId(undefined)
      return
    }

    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(
      resolveText(locale, message.text),
    )
    // Reading Vietnamese text with an en-US voice is unintelligible, so the
    // voice follows the message, not the build.
    utterance.lang = bcp47(locale)
    utterance.rate = 0.95
    const clearSpeakingMessage = () =>
      setSpeakingMessageId((current) =>
        current === message.id ? undefined : current,
      )
    utterance.onend = clearSpeakingMessage
    utterance.onerror = clearSpeakingMessage
    setSpeakingMessageId(message.id)
    window.speechSynthesis.speak(utterance)
  }

  return (
    <aside
      className={`assistant-panel ${open ? 'open' : ''}`}
      inert={!open}
      role="dialog"
      aria-label={t('assistant.a11y.panel')}
    >
      <header className="assistant-header">
        <div className="assistant-identity">
          <span className="assistant-avatar">
            <Sparkles size={18} />
          </span>
          <div>
            <strong>{t('assistant.title')}</strong>
            <span>
              <i />
              {t('assistant.ready')}
            </span>
          </div>
        </div>
        <button className="plain-icon" onClick={onClose} aria-label={t('assistant.a11y.close')}>
          <X size={20} />
        </button>
      </header>

      <div className="assistant-context">
        <MessageCircle size={15} />
        {t('assistant.lookingAt', { count: visibleProducts.length })}
      </div>

      <div className="assistant-messages">
        {messages.map((message) => (
          <div className={`message ${message.role}`} key={message.id}>
            {message.role === 'assistant' && (
              <span className="message-avatar">
                <Bot size={15} />
              </span>
            )}
            <div className="message-content">
              <div className="message-copy">
                <p>{resolveText(locale, message.text)}</p>
                {message.role === 'assistant' && canSpeak && (
                  <button
                    className="message-audio"
                    onClick={() => toggleSpeech(message)}
                    aria-label={
                      speakingMessageId === message.id
                        ? t('assistant.speech.stopLong')
                        : t('assistant.speech.startLong')
                    }
                    title={
                      speakingMessageId === message.id
                        ? t('assistant.speech.stop')
                        : t('assistant.speech.start')
                    }
                  >
                    {speakingMessageId === message.id ? (
                      <VolumeX size={13} />
                    ) : (
                      <Volume2 size={13} />
                    )}
                  </button>
                )}
              </div>
              {message.degraded && (
                <p className="message-degraded" role="status">
                  {t('assistant.degraded')}
                </p>
              )}
              {message.products && message.products.length > 0 && (
                <div className="assistant-product-stack">
                  {message.products.slice(0, 3).map((product) => {
                    const messageActions = (message.actions ?? []).filter(
                      (action) => action.experience_id === product.id,
                    )
                    const productActions =
                      product.actions?.length
                        ? product.actions
                        : messageActions.length
                          ? messageActions
                          : [
                              {
                                type: 'ADD_TO_CART' as const,
                                label: { key: 'assistant.action.addToCart' as const },
                                experience_id: product.id,
                              },
                            ]
                    return (
                      <article className="assistant-product" key={product.id}>
                        <button
                          className="assistant-product-main"
                          onClick={() => onView(product)}
                        >
                          <span className="assistant-product-image">
                            <img
                              src={product.image_url}
                              alt=""
                              onError={(event) => {
                                if (product.fallback_image_url) {
                                  event.currentTarget.src =
                                    product.fallback_image_url
                                }
                              }}
                            />
                            {badgeLabels(product, t)[0] && <i>{badgeLabels(product, t)[0]}</i>}
                          </span>
                          <span className="assistant-product-copy">
                            <strong>{product.title}</strong>
                            <small>
                              <Star size={11} fill="currentColor" />
                              {hasReviews(product) ? `${product.rating.toFixed(1)} · ` : ''}
                              {money(product.currency, product.price)}
                            </small>
                            <em>
                              {product.reason ?? t('assistant.recommendedReason')}
                            </em>
                          </span>
                          <ArrowRight size={17} />
                        </button>
                        <div className="assistant-product-footer">
                          <span className={product.available ? '' : 'sold-out'}>
                            <i />
                            {product.available
                              ? t('assistant.availableOnDate')
                              : t('assistant.soldOut')}
                          </span>
                          <div className="assistant-product-actions">
                            {productActions.slice(0, 3).map((action, index) => (
                              <button
                                key={`${action.type}-${action.slot_id ?? index}`}
                                className={
                                  action.type === 'ADD_TO_CART'
                                    ? 'primary'
                                    : 'secondary'
                                }
                                data-product-id={product.id}
                                aria-label={t('card.a11y.action', {
                                  action: resolveText(locale, action.label),
                                  title: product.title,
                                })}
                                onClick={() => onAction(action, [product])}
                              >
                                {action.type === 'ADD_TO_CART' ? (
                                  <ShoppingBag size={13} />
                                ) : (
                                  <Check size={13} />
                                )}
                                {resolveText(locale, action.label)}
                              </button>
                            ))}
                          </div>
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
              {message.actions?.some(
                (action) =>
                  !action.experience_id ||
                  !message.products?.some(
                    (product) => product.id === action.experience_id,
                  ),
              ) && (
                <div className="assistant-actions">
                  {message.actions
                    .filter(
                      (action) =>
                        !action.experience_id ||
                        !message.products?.some(
                          (product) => product.id === action.experience_id,
                        ),
                    )
                    .map((action, index) => (
                      <button
                        key={`${action.type}-${index}`}
                        className={
                          action.type === 'ADD_TO_CART' ||
                          action.type === 'START_CHECKOUT'
                            ? 'primary-action-card'
                            : 'secondary-action-card'
                        }
                        onClick={() => onAction(action, message.products)}
                      >
                        {action.type === 'ADD_TO_CART' ? (
                          <ShoppingBag size={16} />
                        ) : (
                          <Check size={16} />
                        )}
                        {resolveText(locale, action.label)}
                      </button>
                    ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="message assistant">
            <span className="message-avatar">
              <Bot size={15} />
            </span>
            <div className="assistant-thinking">
              <LoaderCircle size={15} className="spin" />
              {t('assistant.thinking')}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="prompt-row">
        {promptKeys.map((key) => (
          <button key={key} onClick={() => onSend(t(key))} disabled={busy}>
            {t(key)}
          </button>
        ))}
      </div>

      <div className="assistant-input">
        <textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          placeholder={t('assistant.placeholder')}
          rows={2}
        />
        <VoiceInputButton
          label={t('assistant.a11y.voice')}
          stopLabel={t('app.search.voiceStop')}
          disabled={busy}
          onTranscript={(transcript, final) => {
            setValue(transcript)
            if (final && transcript.trim()) {
              onSend(transcript.trim())
              setValue('')
            }
          }}
        />
        <button
          className="assistant-send"
          onClick={submit}
          disabled={!value.trim() || busy}
          aria-label={t('assistant.a11y.send')}
        >
          <Send size={18} />
        </button>
      </div>
      <p className="assistant-disclaimer">
        {t('assistant.footnote')}
      </p>
    </aside>
  )
}
