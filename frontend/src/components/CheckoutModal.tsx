import {
  CalendarCheck,
  Check,
  CreditCard,
  Download,
  LoaderCircle,
  Lock,
  Mail,
  MapPin,
  PartyPopper,
  ShieldCheck,
  User,
  X,
} from 'lucide-react'
import { useState } from 'react'
import type { CartItem, Voucher } from '../types'
import { useLocale, useT } from '../lib/useLocale'
import { formatMoney } from '../lib/format'

type CheckoutModalProps = {
  open: boolean
  items: CartItem[]
  voucher: Voucher | null
  onClose: () => void
  onConfirm: (customer: { name: string; email: string }) => Promise<void>
}

export function CheckoutModal({
  open,
  items,
  voucher,
  onClose,
  onConfirm,
}: CheckoutModalProps) {
  // Formatting locale comes from the provider, not a module constant, so
  // prices re-render when the shopper switches language.
  const { locale } = useLocale()
  const t = useT()
  const money = (currency: string, amount: number) =>
    formatMoney(locale, currency, amount)
  const [name, setName] = useState('Alex Traveller')
  const [email, setEmail] = useState('alex@example.com')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const total = items.reduce((sum, item) => sum + item.total, 0)
  const currency = items[0]?.experience.currency ?? 'USD'

  if (!open) return null

  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      await onConfirm({ name, email })
    } catch {
      setError(
        'We could not complete the simulated booking. Recheck the cart and try again.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-shell" role="dialog" aria-modal="true">
      <button className="modal-backdrop" onClick={onClose} aria-label="Close" />
      <section className="checkout-modal">
        <header>
          <div>
            <span className="eyebrow">{voucher ? 'Booking confirmed' : 'Secure demo checkout'}</span>
            <h2>{voucher ? 'Your Vietnam moments are booked' : 'One last check'}</h2>
          </div>
          <button className="plain-icon" onClick={onClose} aria-label={t('checkout.close')}>
            <X size={20} />
          </button>
        </header>

        {voucher ? (
          <div className="confirmation">
            <div className="success-orbit">
              <PartyPopper size={34} />
            </div>
            <p>
              Everything is ready. Your mobile vouchers are grouped under one
              easy booking reference.
            </p>
            <div className="voucher-card">
              <div className="voucher-brand">
                <span className="brand-mark">V</span>
                <strong>VIETRA</strong>
                <small>Central Vietnam collection</small>
              </div>
              {voucher.qr_image_data_url ? (
                <img
                  className="voucher-qr-image"
                  src={voucher.qr_image_data_url}
                  alt="Booking voucher QR code"
                />
              ) : (
                <div className="fake-qr" aria-label="Demo QR code">
                  {Array.from({ length: 64 }, (_, index) => (
                    <i key={index} className={(index * 7 + index % 5) % 3 === 0 ? 'on' : ''} />
                  ))}
                </div>
              )}
              <div className="voucher-details">
                <span>{t('checkout.reference')}</span>
                <strong>{voucher.booking_reference}</strong>
                <small>{voucher.voucher_reference}</small>
              </div>
            </div>
            <div className="confirmation-actions">
              <button className="checkout-button" onClick={() => window.print()}>
                <Download size={18} />
                Save voucher
              </button>
              <button className="secondary-button" onClick={onClose}>
                Keep exploring
              </button>
            </div>
          </div>
        ) : (
          <div className="checkout-layout">
            <div className="checkout-form">
              <section>
                <div className="section-heading">
                  <span>1</span>
                  <div>
                    <h3>{t('checkout.leadTraveller')}</h3>
                    <p>{t('checkout.emailNote')}</p>
                  </div>
                </div>
                <label>
                  <span>
                    <User size={15} /> Full name
                  </span>
                  <input value={name} onChange={(event) => setName(event.target.value)} />
                </label>
                <label>
                  <span>
                    <Mail size={15} /> Email
                  </span>
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </label>
              </section>

              <section>
                <div className="section-heading">
                  <span>2</span>
                  <div>
                    <h3>{t('checkout.demoPayment')}</h3>
                    <p>{t('checkout.demoNote')}</p>
                  </div>
                </div>
                <div className="demo-payment-card">
                  <CreditCard size={24} />
                  <span>
                    <strong>{t('checkout.wallet')}</strong>
                    <small>•••• 4242 · Always approved</small>
                  </span>
                  <Check size={18} />
                </div>
              </section>

              <div className="secure-note">
                <Lock size={16} />
                This is a simulated purchase for the proof of concept.
              </div>
            </div>

            <aside className="order-summary">
              <h3>{t('checkout.title')}</h3>
              {items.map((item) => (
                <div className="summary-item" key={item.id}>
                  <img
                    src={item.experience.image_url}
                    alt=""
                    onError={(event) => {
                      if (item.experience.fallback_image_url) {
                        event.currentTarget.src = item.experience.fallback_image_url
                      }
                    }}
                  />
                  <div>
                    <strong>{item.experience.title}</strong>
                    <span>
                      <CalendarCheck size={13} />
                      {item.date} · {item.time}
                    </span>
                    <span>
                      <MapPin size={13} />
                      {item.experience.location}
                    </span>
                  </div>
                  <b>
                    {money(currency, item.total)}
                  </b>
                </div>
              ))}
              <div className="summary-total">
                <span>{t('checkout.total')}</span>
                <strong>
                  {money(currency, total)}
                </strong>
              </div>
              {error && (
                <p className="checkout-error" role="alert">
                  {error}
                </p>
              )}
              <button
                className="checkout-button"
                onClick={confirm}
                disabled={busy || !name || !email}
              >
                {busy ? <LoaderCircle size={18} className="spin" /> : <ShieldCheck size={18} />}
                {busy ? 'Confirming…' : 'Confirm demo purchase'}
              </button>
              <p className="fine-print">
                By confirming, you accept the mock supplier terms and
                cancellation policies shown for each experience.
              </p>
            </aside>
          </div>
        )}
      </section>
    </div>
  )
}
