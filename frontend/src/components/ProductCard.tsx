import {
  ArrowUpRight,
  Check,
  Clock3,
  Heart,
  MapPin,
  ShoppingBag,
  Sparkles,
  Star,
} from 'lucide-react'
import { useState } from 'react'
import type { Experience } from '../types'

type ProductCardProps = {
  product: Experience
  compact?: boolean
  onView: (product: Experience) => void
  onAdd: (product: Experience) => void
}

const durationLabel = (minutes: number) => {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours}h ${remainder}m` : `${hours} hours`
}

const money = (currency: string, amount: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: currency === 'VND' ? 0 : 2,
  }).format(amount)

export function ProductCard({
  product,
  compact = false,
  onView,
  onAdd,
}: ProductCardProps) {
  const [saved, setSaved] = useState(false)

  return (
    <article className={`product-card ${compact ? 'compact' : ''}`}>
      <button
        className="product-image-button"
        onClick={() => onView(product)}
        aria-label={`View ${product.title}`}
      >
        <img
          className="product-image"
          src={product.image_url}
          alt={product.title}
          onError={(event) => {
            if (product.fallback_image_url) {
              event.currentTarget.src = product.fallback_image_url
            }
          }}
        />
        <span className="image-shade" />
        <span className="product-location">
          <MapPin size={13} />
          {product.location}
        </span>
      </button>

      <button
        className={`save-button ${saved ? 'saved' : ''}`}
        onClick={() => setSaved((value) => !value)}
        aria-label={saved ? 'Remove from saved' : 'Save experience'}
      >
        <Heart size={18} fill={saved ? 'currentColor' : 'none'} />
      </button>

      <div className="product-body">
        <div className="badge-row">
          {product.badges.slice(0, 2).map((badge) => (
            <span className="mini-badge" key={badge}>
              {badge === 'Top pick' || badge === 'Guest favourite' ? (
                <Sparkles size={11} />
              ) : (
                <Check size={11} />
              )}
              {badge}
            </span>
          ))}
        </div>
        <button
          className="product-title-button"
          onClick={() => onView(product)}
        >
          <h3>{product.title}</h3>
        </button>
        {!compact && <p className="product-description">{product.short_description}</p>}
        {product.reason && (
          <p className="match-reason">
            <Sparkles size={14} />
            {product.reason}
          </p>
        )}
        <div className="product-meta">
          <span className="rating">
            <Star size={14} fill="currentColor" />
            <strong>{product.rating.toFixed(1)}</strong>
            <span>({product.review_count.toLocaleString()})</span>
          </span>
          <span>
            <Clock3 size={14} />
            {durationLabel(product.duration_minutes)}
          </span>
        </div>
        <div className="product-footer">
          <div className="price">
            <span>From</span>
            <strong>
              {money(product.currency, product.price)}
            </strong>
            <span>per guest</span>
          </div>
          <div className="product-actions">
            <button
              className="icon-action"
              onClick={() => onView(product)}
              aria-label="View details"
            >
              <ArrowUpRight size={18} />
            </button>
            <button className="add-button" onClick={() => onAdd(product)}>
              <ShoppingBag size={16} />
              Add
            </button>
          </div>
        </div>
      </div>
    </article>
  )
}
