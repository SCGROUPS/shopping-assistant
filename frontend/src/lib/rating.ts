import type { Experience } from '../types'

/**
 * Imported supplier inventory arrives with no review history. Rendering that as
 * "0.0 ★ (0)" reads as a terrible experience rather than a new one, so every
 * surface asks here before showing a score.
 */
export const hasReviews = (product: Pick<Experience, 'review_count'>) =>
  product.review_count > 0

export const NEW_LISTING_LABEL = 'Newly listed'
