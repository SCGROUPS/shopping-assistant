import {
  ArrowRight,
  CalendarDays,
  Minus,
  ShieldCheck,
  ShoppingBag,
  Trash2,
  X,
} from 'lucide-react'
import type { CartItem } from '../types'

const money = (currency: string, amount: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

type CartDrawerProps = {
  open: boolean
  items: CartItem[]
  onClose: () => void
  onRemove: (id: string) => void
  onCheckout: () => void
}

export function CartDrawer({
  open,
  items,
  onClose,
  onRemove,
  onCheckout,
}: CartDrawerProps) {
  const total = items.reduce((sum, item) => sum + item.total, 0)
  const currency = items[0]?.experience.currency ?? 'USD'

  return (
    <>
      {open && (
        <button
          className="drawer-backdrop visible"
          onClick={onClose}
          aria-label="Close cart"
        />
      )}
      <aside
        className={`cart-drawer ${open ? 'open' : ''}`}
        inert={!open}
        role="dialog"
        aria-label="Experience cart"
      >
        <header>
          <div>
            <span className="eyebrow">Your trip</span>
            <h2>Experience cart</h2>
          </div>
          <button className="plain-icon" onClick={onClose} aria-label="Close cart">
            <X size={20} />
          </button>
        </header>

        {items.length === 0 ? (
          <div className="empty-cart">
            <span>
              <ShoppingBag size={28} />
            </span>
            <h3>Your adventure starts here</h3>
            <p>Add an experience and Mai can help complete your day.</p>
          </div>
        ) : (
          <>
            <div className="cart-items">
              {items.map((item) => (
                <article className="cart-item" key={item.id}>
                  <img
                    src={item.experience.image_url}
                    alt=""
                    onError={(event) => {
                      if (item.experience.fallback_image_url) {
                        event.currentTarget.src =
                          item.experience.fallback_image_url
                      }
                    }}
                  />
                  <div>
                    <strong>{item.experience.title}</strong>
                    <span>
                      <CalendarDays size={13} />
                      {item.date} {item.time ? `· ${item.time}` : ''}
                    </span>
                    <span>
                      {item.adults} adults
                      {item.children ? ` · ${item.children} children` : ''}
                    </span>
                    <b>
                      {money(item.experience.currency, item.total)}
                    </b>
                  </div>
                  <button
                    className="remove-item"
                    onClick={() => onRemove(item.id)}
                    aria-label={`Remove ${item.experience.title}`}
                  >
                    <Trash2 size={16} />
                  </button>
                </article>
              ))}
            </div>

            <div className="cart-assurance">
              <ShieldCheck size={20} />
              <span>
                <strong>Free cancellation on every item</strong>
                <small>We recheck price and availability before booking.</small>
              </span>
            </div>

            <footer className="cart-footer">
              <div className="total-line">
                <span>
                  Total
                  <small>Taxes included</small>
                </span>
                <strong>
                  {money(currency, total)}
                </strong>
              </div>
              <button className="checkout-button" onClick={onCheckout}>
                Continue to checkout
                <ArrowRight size={18} />
              </button>
              <p>
                <Minus size={13} /> No real payment will be charged in this demo.
              </p>
            </footer>
          </>
        )}
      </aside>
    </>
  )
}
