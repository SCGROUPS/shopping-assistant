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
import { VoiceInputButton } from './VoiceInputButton'

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

const prompts = [
  'Plan a relaxed half-day',
  'Best for a family?',
  'What works if it rains?',
  'Find accessible options',
]

const money = (currency: string, amount: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

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
    const utterance = new SpeechSynthesisUtterance(message.text)
    utterance.lang = 'en-US'
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
      aria-label="Mai shopping assistant"
    >
      <header className="assistant-header">
        <div className="assistant-identity">
          <span className="assistant-avatar">
            <Sparkles size={18} />
          </span>
          <div>
            <strong>Mai, your local trip curator</strong>
            <span>
              <i />
              Ready to help
            </span>
          </div>
        </div>
        <button className="plain-icon" onClick={onClose} aria-label="Close assistant">
          <X size={20} />
        </button>
      </header>

      <div className="assistant-context">
        <MessageCircle size={15} />
        Looking at {visibleProducts.length} experiences in Central Vietnam
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
                <p>{message.text}</p>
                {message.role === 'assistant' && canSpeak && (
                  <button
                    className="message-audio"
                    onClick={() => toggleSpeech(message)}
                    aria-label={
                      speakingMessageId === message.id
                        ? 'Stop reading this response'
                        : 'Read this response aloud'
                    }
                    title={
                      speakingMessageId === message.id
                        ? 'Stop reading'
                        : 'Listen to response'
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
                                label: 'Add to trip',
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
                            {product.badges[0] && <i>{product.badges[0]}</i>}
                          </span>
                          <span className="assistant-product-copy">
                            <strong>{product.title}</strong>
                            <small>
                              <Star size={11} fill="currentColor" />
                              {product.rating.toFixed(1)} ·{' '}
                              {money(product.currency, product.price)}
                            </small>
                            <em>
                              {product.reason ?? 'Recommended for this trip'}
                            </em>
                          </span>
                          <ArrowRight size={17} />
                        </button>
                        <div className="assistant-product-footer">
                          <span>
                            <i />
                            Available on your date
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
                                aria-label={`${action.label} for ${product.title}`}
                                onClick={() => onAction(action, [product])}
                              >
                                {action.type === 'ADD_TO_CART' ? (
                                  <ShoppingBag size={13} />
                                ) : (
                                  <Check size={13} />
                                )}
                                {action.label}
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
                        {action.label}
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
              Checking fit, timing, and availability…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="prompt-row">
        {prompts.map((prompt) => (
          <button key={prompt} onClick={() => onSend(prompt)} disabled={busy}>
            {prompt}
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
          placeholder="Ask about timing, access, prices, or build a plan…"
          rows={2}
        />
        <VoiceInputButton
          label="Talk to Mai"
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
          aria-label="Send"
        >
          <Send size={18} />
        </button>
      </div>
      <p className="assistant-disclaimer">
        Mai checks catalog facts and live demo availability before taking action.
      </p>
    </aside>
  )
}
