import {
  ArrowRight,
  CalendarDays,
  MessageCircle,
  Minus,
  Plus,
  ShieldCheck,
  ShoppingBag,
  Trash2,
  X,
} from 'lucide-react'
import type { CartItem, Experience } from '../types'
import { useLocale, useT } from '../lib/useLocale'
import { formatMoney } from '../lib/format'

type CartDrawerProps = {
  open: boolean
  items: CartItem[]
  onClose: () => void
  onRemove: (id: string) => void
  onCheckout: () => void
  onCheckPlan?: () => void
  clashing: boolean
  /** Attach-rate rail. Highest-intent moment in the funnel. */
  crossSell?: Experience[]
  onAddCrossSell?: (product: Experience) => void
}

export function CartDrawer({
  open,
  items,
  onClose,
  onRemove,
  onCheckPlan,
  clashing,
  onCheckout,
  crossSell = [],
  onAddCrossSell,
}: CartDrawerProps) {
  // Formatting locale comes from the provider, not a module constant, so
  // prices re-render when the shopper switches language.
  const { locale } = useLocale()
  const t = useT()
  const money = (currency: string, amount: number) =>
    formatMoney(locale, currency, amount)
  const total = items.reduce((sum, item) => sum + item.total, 0)
  const currency = items[0]?.experience.currency ?? 'USD'

  return (
    <>
      {open && (
        <button
          className="drawer-backdrop visible"
          onClick={onClose}
          aria-label={t('cart.close')}
        />
      )}
      <aside
        className={`cart-drawer ${open ? 'open' : ''}`}
        inert={!open}
        role="dialog"
        aria-label={t('cart.title')}
      >
        <header>
          <div>
            <span className="eyebrow">{t('cart.eyebrow')}</span>
            <h2>{t('cart.title')}</h2>
          </div>
          <button className="plain-icon" onClick={onClose} aria-label={t('cart.close')}>
            <X size={20} />
          </button>
        </header>

        {items.length === 0 ? (
          <div className="empty-cart">
            <span>
              <ShoppingBag size={28} />
            </span>
            <h3>{t('cart.empty.title')}</h3>
            <p>{t('cart.empty.body')}</p>
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
                      {t.plural('cart.adults', item.adults)}
                      {item.children
                        ? ` · ${t.plural('cart.children', item.children)}`
                        : ''}
                    </span>
                    <b>
                      {money(item.experience.currency, item.total)}
                    </b>
                  </div>
                  <button
                    className="remove-item"
                    onClick={() => onRemove(item.id)}
                    aria-label={t('card.a11y.remove', { title: item.experience.title })}
                  >
                    <Trash2 size={16} />
                  </button>
                </article>
              ))}
            </div>

            {crossSell.length > 0 && onAddCrossSell && (
              <section className="cart-cross-sell">
                <h3>{t('cart.crossSell')}</h3>
                <ul>
                  {crossSell.slice(0, 3).map((product) => (
                    <li key={product.id}>
                      <img src={product.image_url} alt="" loading="lazy" />
                      <div>
                        <strong>{product.title}</strong>
                        <small>
                          {product.display_price != null &&
                          product.display_currency
                            ? money(
                                product.display_currency,
                                product.display_price,
                              )
                            : money(product.currency, product.price)}
                          {product.scarcity ? ` · ${product.scarcity}` : ''}
                        </small>
                      </div>
                      <button
                        onClick={() => onAddCrossSell(product)}
                        aria-label={t('card.a11y.add', { title: product.title })}
                      >
                        <Plus size={16} />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {onCheckPlan && (
              <button className="cart-plan-check" onClick={onCheckPlan}>
                <MessageCircle size={16} />
                {clashing ? t('cart.clash') : t('cart.checkPlan')}
              </button>
            )}

            <div className="cart-assurance">
              <ShieldCheck size={20} />
              <span>
                <strong>{t('cart.cancellation')}</strong>
                <small>{t('cart.assurance.recheck')}</small>
              </span>
            </div>

            <footer className="cart-footer">
              <div className="total-line">
                <span>
                  {t('cart.total')}
                  <small>{t('cart.taxes')}</small>
                </span>
                <strong>
                  {money(currency, total)}
                </strong>
              </div>
              <button className="checkout-button" onClick={onCheckout}>
                {t('cart.continueCheckout')}
                <ArrowRight size={18} />
              </button>
              <p>
                <Minus size={13} /> {t('cart.demoNote')}
              </p>
            </footer>
          </>
        )}
      </aside>
    </>
  )
}
