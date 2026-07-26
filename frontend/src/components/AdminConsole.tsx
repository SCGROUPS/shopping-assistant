// The operator console.
//
// Deliberately plain. This is a staff tool used all day by people who need to
// see state and change it, so it favours density and directness over the
// storefront's warmth. Every control here is gated by a capability the server
// enforces independently; hiding a button the server would reject is a
// courtesy, not the security boundary.

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AdminError,
  changeMerchandising,
  changeStatus,
  clearOverride,
  approveTranslation,
  editTranslation,
  fetchAudit,
  fetchCatalog,
  fetchExperience,
  fetchFunnel,
  fetchOverview,
  fetchSettings,
  fetchTranslationCoverage,
  fetchTranslationQueue,
  getApiKey,
  patchExperience,
  rejectTranslation,
  resetSetting,
  saveSetting,
  setApiKey,
  whoami,
} from '../lib/adminApi'
import type {
  AuditEntry,
  CatalogFilters,
  CatalogPage,
  CatalogRow,
  Capability,
  FunnelView,
  Principal,
  ReviewSummary,
  SettingView,
  TranslationCandidate,
  TranslationReviewQueue,
  LocaleCoverage,
  ReviewState,
  PublishBlocker,
} from '../lib/adminApi'

type Tab = 'review' | 'catalog' | 'translations' | 'settings' | 'audit' | 'insight'

const TABS: Array<{ id: Tab; label: string; needs?: Capability }> = [
  { id: 'review', label: 'Review queue' },
  { id: 'catalog', label: 'Catalogue' },
  { id: 'translations', label: 'Languages' },
  { id: 'settings', label: 'Configuration', needs: 'configure' },
  { id: 'audit', label: 'Audit' },
  { id: 'insight', label: 'Performance' },
]

const STATUSES = ['DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'ARCHIVED']

const EDITABLE_TEXT: Array<{ field: string; label: string; long?: boolean }> = [
  { field: 'title', label: 'Title' },
  { field: 'short_description', label: 'Short description', long: true },
  { field: 'description', label: 'Description', long: true },
  { field: 'category', label: 'Category' },
  { field: 'meeting_point', label: 'Meeting point' },
]

const vnd = (value: number | null | undefined) =>
  value == null ? '—' : `₫${Math.round(value).toLocaleString('en-US')}`

const when = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : '—'

function SignIn({ onSignedIn }: { onSignedIn: (who: Principal) => void }) {
  const [key, setKey] = useState(getApiKey())
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setApiKey(key)
    try {
      onSignedIn(await whoami())
    } catch (problem) {
      // 401 and "backend is down" are different problems with different
      // fixes, so they get different sentences.
      const status = problem instanceof AdminError ? problem.status : 0
      setError(
        status === 401
          ? 'That key is not recognised.'
          : `Could not reach the console API (${status || 'network error'}).`,
      )
      setApiKey('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ops-signin">
      <form onSubmit={submit}>
        <h1>Vietra operations</h1>
        <p>Sign in with your operator key.</p>
        <input
          type="password"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder="vk_…"
          aria-label="Operator key"
          autoFocus
        />
        <button type="submit" disabled={busy || !key.trim()}>
          {busy ? 'Checking…' : 'Sign in'}
        </button>
        {error ? <p className="ops-error">{error}</p> : null}
      </form>
    </div>
  )
}

function Row({
  row,
  can,
  onSelect,
  selected,
}: {
  row: CatalogRow
  can: (capability: Capability) => boolean
  onSelect: () => void
  selected: boolean
}) {
  return (
    <tr
      className={`${selected ? 'is-selected' : ''} ${row.needs_review ? 'needs-review' : ''}`}
      onClick={onSelect}
    >
      <td>
        <strong>{row.title}</strong>
        <span className="ops-sub">
          {row.destination} · {row.category}
          {row.supplier ? ` · ${row.supplier}` : ''}
        </span>
      </td>
      <td>
        <span className={`ops-status ops-status-${row.status.toLowerCase()}`}>
          {row.status.replace('_', ' ').toLowerCase()}
        </span>
        {row.needs_review ? <span className="ops-flag">needs review</span> : null}
      </td>
      <td>{vnd(row.price)}</td>
      <td>{row.rating.toFixed(1)}</td>
      <td>
        {row.suppressed ? <span className="ops-flag ops-flag-off">hidden</span> : null}
        {row.pinned ? <span className="ops-flag">pinned</span> : null}
        {row.boost !== 1 ? <span className="ops-flag">×{row.boost.toFixed(2)}</span> : null}
        {row.promotion_label ? (
          <span className="ops-flag">{row.promotion_label}</span>
        ) : null}
      </td>
      <td>
        {row.overridden_fields.length ? (
          <span
            className="ops-flag ops-flag-edit"
            title={`Protected from re-import: ${row.overridden_fields.join(', ')}`}
          >
            {row.overridden_fields.length} edited
          </span>
        ) : (
          <span className="ops-sub">supplier</span>
        )}
      </td>
      <td className="ops-actions-cell">{can('catalog:write') ? 'Open' : 'View'}</td>
    </tr>
  )
}

function Editor({
  row,
  can,
  ceiling,
  onChanged,
  onClose,
}: {
  row: CatalogRow
  can: (capability: Capability) => boolean
  ceiling: number
  onChanged: (row: CatalogRow) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [history, setHistory] = useState<AuditEntry[]>([])

  useEffect(() => {
    setDraft({})
    setError(null)
    let live = true
    void (async () => {
      try {
        const [full, audit] = await Promise.all([
          fetchExperience(row.id),
          fetchAudit(row.id, 10),
        ])
        if (!live) return
        setDetail(full)
        setHistory(audit.entries)
      } catch {
        if (live) setDetail(null)
      }
    })()
    return () => {
      live = false
    }
  }, [row.id])

  const run = async (task: () => Promise<CatalogRow>) => {
    setBusy(true)
    setError(null)
    try {
      onChanged(await task())
      setDraft({})
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  const current = (field: string) =>
    draft[field] ?? String((detail?.[field] as string | undefined) ?? '')

  const dirty = Object.keys(draft).length > 0

  // Recomputed server-side on every read, because deactivating an option or a
  // reindex falling behind can break a listing without anybody editing it.
  const blockers = (detail?.publish_blockers as PublishBlocker[] | undefined) ?? []

  return (
    <aside className="ops-editor">
      <header>
        <div>
          <h2>{row.title}</h2>
          <span className="ops-sub">
            {row.slug} · {row.destination}
          </span>
        </div>
        <button type="button" onClick={onClose} aria-label="Close editor">
          ✕
        </button>
      </header>

      {row.needs_review ? (
        <div className="ops-callout">
          <strong>Flagged on import.</strong>
          <span>{row.review_note || 'The importer was not confident about this record.'}</span>
        </div>
      ) : null}

      {row.overridden_fields.length ? (
        <div className="ops-callout ops-callout-quiet">
          <strong>Protected from re-import</strong>
          <span>
            The supplier feed will no longer overwrite these. Release one to let
            the next import manage it again.
          </span>
          <div className="ops-chips">
            {row.overridden_fields.map((field) => (
              <span key={field} className="ops-chip">
                {field}
                <button
                  type="button"
                  disabled={busy || !can('catalog:write')}
                  title={`Release ${field} back to the supplier feed`}
                  onClick={() => void run(() => clearOverride(row.id, field))}
                >
                  release
                </button>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <section>
        <h3>Details</h3>
        {EDITABLE_TEXT.map(({ field, label, long }) => (
          <label key={field} className="ops-field">
            <span>
              {label}
              {row.overridden_fields.includes(field) ? (
                <em className="ops-sub"> (edited)</em>
              ) : null}
            </span>
            {long ? (
              <textarea
                rows={3}
                value={current(field)}
                disabled={!can('catalog:write') || !detail}
                onChange={(event) =>
                  setDraft({ ...draft, [field]: event.target.value })
                }
              />
            ) : (
              <input
                value={current(field)}
                disabled={!can('catalog:write') || !detail}
                onChange={(event) =>
                  setDraft({ ...draft, [field]: event.target.value })
                }
              />
            )}
          </label>
        ))}
        <button
          type="button"
          disabled={!dirty || busy || !can('catalog:write')}
          onClick={() => void run(() => patchExperience(row.id, draft))}
        >
          {busy ? 'Saving…' : 'Save details'}
        </button>
      </section>

      <section>
        <h3>Status</h3>
        <p className="ops-sub">
          Only published experiences appear in search or the assistant.
        </p>
        <input
          value={note}
          placeholder="Why (recorded in the audit trail)"
          onChange={(event) => setNote(event.target.value)}
        />
        <div className="ops-buttons">
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={row.status === status ? 'is-current' : ''}
              disabled={
                busy ||
                row.status === status ||
                !can('catalog:publish') ||
                (status === 'PUBLISHED' && blockers.length > 0)
              }
              onClick={() => void run(() => changeStatus(row.id, status, note))}
            >
              {status.replace('_', ' ').toLowerCase()}
            </button>
          ))}
        </div>
        {blockers.length > 0 ? (
          // Shown before the operator clicks, not after the server refuses.
          // A disabled button with no explanation is indistinguishable from a
          // broken one.
          <div className="ops-blockers">
            <p className="ops-sub">
              {row.status === 'PUBLISHED'
                ? 'This is live and would not be allowed to publish today:'
                : 'Before this can be published:'}
            </p>
            <ul>
              {blockers.map((blocker) => (
                <li key={blocker.code}>{blocker.message}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section>
        <h3>Merchandising</h3>
        <p className="ops-sub">
          A boost is a thumb on the scale, capped at ×{ceiling.toFixed(2)} so it
          cannot outrank relevance outright.
        </p>
        <label className="ops-field">
          <span>Boost ({row.boost.toFixed(2)})</span>
          <input
            type="range"
            min={Number((1 / ceiling).toFixed(2))}
            max={ceiling}
            step={0.05}
            value={row.boost}
            disabled={busy || !can('merchandise')}
            onChange={(event) =>
              void run(() =>
                changeMerchandising(row.id, { boost: Number(event.target.value) }),
              )
            }
          />
        </label>
        <div className="ops-buttons">
          <button
            type="button"
            className={row.pinned ? 'is-current' : ''}
            disabled={busy || !can('merchandise')}
            onClick={() =>
              void run(() => changeMerchandising(row.id, { pinned: !row.pinned }))
            }
          >
            {row.pinned ? 'Unpin' : 'Pin to top'}
          </button>
          <button
            type="button"
            className={row.suppressed ? 'is-current' : ''}
            disabled={busy || !can('merchandise')}
            onClick={() =>
              void run(() =>
                changeMerchandising(row.id, { suppressed: !row.suppressed }),
              )
            }
          >
            {row.suppressed ? 'Restore to storefront' : 'Hide from storefront'}
          </button>
        </div>
        <p className="ops-sub">
          Pinning applies to the recommended sort only. Under an explicit price
          or rating sort it is ignored, because overriding the control the
          shopper just used would be a lie.
        </p>
      </section>

      {history.length ? (
        <section>
          <h3>Recent changes</h3>
          <ul className="ops-history">
            {history.map((entry) => (
              <li key={entry.id}>
                <span className="ops-sub">{when(entry.occurred_at)}</span>
                <strong>{entry.operator}</strong> {entry.action}
                {Object.keys(entry.changes).length ? (
                  <span className="ops-sub">
                    {' '}
                    ({Object.keys(entry.changes).join(', ')})
                  </span>
                ) : null}
                {entry.summary ? <em> — {entry.summary}</em> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {error ? <p className="ops-error">{error}</p> : null}
    </aside>
  )
}

function Settings({ can }: { can: (capability: Capability) => boolean }) {
  const [settings, setSettings] = useState<SettingView[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [status, setStatus] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    const payload = await fetchSettings()
    setSettings(payload.settings)
    setDrafts({})
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const commit = async (key: string) => {
    setStatus({ ...status, [key]: 'saving' })
    try {
      const parsed = JSON.parse(drafts[key])
      await saveSetting(key, parsed)
      await load()
      setStatus({ ...status, [key]: 'saved' })
    } catch (problem) {
      setStatus({
        ...status,
        [key]:
          problem instanceof SyntaxError
            ? 'That is not valid JSON.'
            : problem instanceof Error
              ? problem.message
              : 'Rejected',
      })
    }
  }

  return (
    <div className="ops-settings">
      <p className="ops-sub">
        These take effect within about thirty seconds, with no deploy. A value
        the server rejects is never stored, and a stored value that stops
        validating is ignored rather than taking ranking down.
      </p>
      {settings.map((setting) => (
        <article key={setting.key}>
          <header>
            <h3>{setting.key}</h3>
            {setting.overridden ? (
              <span className="ops-flag">changed · v{setting.version}</span>
            ) : (
              <span className="ops-sub">default</span>
            )}
          </header>
          <p className="ops-sub">{setting.description}</p>
          <textarea
            rows={Math.min(
              16,
              (drafts[setting.key] ?? JSON.stringify(setting.value, null, 2)).split(
                '\n',
              ).length + 1,
            )}
            disabled={!can('configure')}
            value={drafts[setting.key] ?? JSON.stringify(setting.value, null, 2)}
            onChange={(event) =>
              setDrafts({ ...drafts, [setting.key]: event.target.value })
            }
          />
          <div className="ops-buttons">
            <button
              type="button"
              disabled={!can('configure') || drafts[setting.key] === undefined}
              onClick={() => void commit(setting.key)}
            >
              Save
            </button>
            <button
              type="button"
              disabled={!can('configure') || !setting.overridden}
              onClick={() => void resetSetting(setting.key).then(load)}
            >
              Reset to default
            </button>
            {status[setting.key] ? (
              <span
                className={
                  status[setting.key] === 'saved' ? 'ops-sub' : 'ops-error'
                }
              >
                {status[setting.key]}
              </span>
            ) : null}
          </div>
        </article>
      ))}
    </div>
  )
}

function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([])

  useEffect(() => {
    void fetchAudit(undefined, 100).then((payload) => setEntries(payload.entries))
  }, [])

  return (
    <table className="ops-table">
      <thead>
        <tr>
          <th>When</th>
          <th>Who</th>
          <th>Did what</th>
          <th>To</th>
          <th>Changes</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.id}>
            <td className="ops-sub">{when(entry.occurred_at)}</td>
            <td>{entry.operator}</td>
            <td>{entry.action}</td>
            <td className="ops-sub">
              {entry.entity_type} {entry.entity_id.slice(0, 8)}
            </td>
            <td>
              {Object.entries(entry.changes).map(([field, delta]) => (
                <div key={field} className="ops-sub">
                  <strong>{field}</strong>: {JSON.stringify(delta.from)} →{' '}
                  {JSON.stringify(delta.to)}
                </div>
              ))}
              {entry.summary ? <em>{entry.summary}</em> : null}
            </td>
          </tr>
        ))}
        {entries.length === 0 ? (
          <tr>
            <td colSpan={5} className="ops-sub">
              Nothing has been changed yet.
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  )
}

const LOCALE_NAMES: Record<string, string> = {
  vi: 'Tiếng Việt',
  zh: '中文',
  ja: '日本語',
  ko: '한국어',
  fr: 'Français',
  de: 'Deutsch',
  es: 'Español',
  en: 'English',
}

/**
 * Machine translations held back from the storefront, and the only way to
 * release one.
 *
 * `meeting_point` is held for review because a mistranslated set of directions
 * sends a traveller to the wrong place. The pipeline implemented the holding
 * and nothing implemented the release, so every locale sat permanently at one
 * unpublished field per experience while the coverage report called it a
 * backlog.
 *
 * There is deliberately no "approve all". A single click over hundreds of
 * distinct sets of directions is not review - it is the automatic publication
 * this field is specifically excluded from, with an operator's name attached.
 */
function Translations({ can }: { can: (capability: Capability) => boolean }) {
  const [queue, setQueue] = useState<TranslationReviewQueue | null>(null)
  const [coverage, setCoverage] = useState<LocaleCoverage[]>([])
  const [locale, setLocale] = useState<string>('')
  const [state, setState] = useState<ReviewState>('needs_review')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const mayPublish = can('catalog:publish')
  const keyOf = (item: TranslationCandidate) =>
    `${item.experience_id}:${item.field}:${item.locale}`

  const load = useCallback(async () => {
    setError(null)
    try {
      const [next, cover] = await Promise.all([
        fetchTranslationQueue(locale || undefined, state),
        fetchTranslationCoverage(),
      ])
      setQueue(next)
      setCoverage(cover.locales)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not load the queue')
    }
  }, [locale, state])

  useEffect(() => {
    void load()
  }, [load])

  const act = async (item: TranslationCandidate, what: 'approve' | 'reject') => {
    setBusy(keyOf(item))
    setError(null)
    try {
      await (what === 'approve' ? approveTranslation(item) : rejectTranslation(item))
      await load()
    } catch (problem) {
      // A 409 means the text moved after it was rendered. Reloading is the
      // whole remedy: the reviewer needs to read the new version, not retry.
      setError(problem instanceof Error ? problem.message : 'The decision did not apply')
      await load()
    } finally {
      setBusy(null)
    }
  }

  const saveEdit = async (item: TranslationCandidate) => {
    setBusy(keyOf(item))
    setError(null)
    try {
      await editTranslation(item, draft)
      setEditing(null)
      setDraft('')
      await load()
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not save')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="ops-translations">
      <table className="ops-table ops-coverage">
        <thead>
          <tr>
            <th>Language</th>
            <th>Live</th>
            <th>Translated</th>
            <th>Awaiting review</th>
            <th>Showing English</th>
          </tr>
        </thead>
        <tbody>
          {coverage.map((row) => (
            <tr key={row.locale}>
              <td>
                {LOCALE_NAMES[row.locale] ?? row.locale}{' '}
                <span className="ops-sub">{row.locale}</span>
              </td>
              <td>{row.enabled ? 'Yes' : 'No'}</td>
              <td>{row.percent.toFixed(1)}%</td>
              <td>{row.needs_review}</td>
              <td>{row.fallback}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="ops-filters">
        {/* Rejected fields must be reachable. Rejecting deliberately does not
            re-enqueue, so a console that lists only `needs_review` strands
            every rejection on English permanently - which is the defect this
            whole screen exists to fix, one layer up. */}
        <select
          value={state}
          onChange={(event) => setState(event.target.value as ReviewState)}
        >
          <option value="needs_review">Awaiting a decision</option>
          <option value="rejected">Rejected, needs writing</option>
        </select>
        <select value={locale} onChange={(event) => setLocale(event.target.value)}>
          <option value="">Every language</option>
          {Object.entries(queue?.by_locale ?? {}).map(([code, count]) => (
            <option key={code} value={code}>
              {LOCALE_NAMES[code] ?? code} ({count})
            </option>
          ))}
        </select>
        <span className="ops-sub">
          {queue ? `${queue.total} awaiting a decision` : 'Loading…'}
        </span>
      </div>

      {error ? <p className="ops-error">{error}</p> : null}
      {!mayPublish ? (
        <p className="ops-sub">
          You can read this queue but not decide on it. Publishing a translation
          needs the catalogue publishing capability.
        </p>
      ) : null}

      {queue?.items.map((item) => {
        const key = keyOf(item)
        return (
          <article key={key} className="ops-candidate">
            <header>
              <strong>{item.title}</strong>
              <span className="ops-sub">
                {item.field} · {LOCALE_NAMES[item.locale] ?? item.locale}
              </span>
            </header>
            <div className="ops-candidate-pair">
              <div>
                <span className="ops-sub">Source ({item.source_language})</span>
                <p>{item.source_text}</p>
              </div>
              <div>
                <span className="ops-sub">
                  {item.status === 'rejected'
                    ? `Rejected by ${item.reviewed_by || 'an operator'}`
                    : `Proposed (${item.locale})`}
                </span>
                <p lang={item.locale}>
                  {item.candidate_value || (
                    <em>
                      Nothing is published in this language. Write the
                      translation, or shoppers keep seeing the English.
                    </em>
                  )}
                </p>
              </div>
            </div>
            {!item.answers_current_source ? (
              <p className="ops-warning">
                The source text changed after this was translated. It cannot be
                approved — reject it, or write the translation yourself.
              </p>
            ) : null}
            {editing === key ? (
              <div className="ops-candidate-edit">
                <textarea
                  value={draft}
                  lang={item.locale}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={3}
                />
                <button
                  type="button"
                  disabled={busy === key || !draft.trim()}
                  onClick={() => void saveEdit(item)}
                >
                  Publish my version
                </button>
                <button type="button" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <div className="ops-candidate-actions">
                {item.status === 'needs_review' ? (
                  <>
                    <button
                      type="button"
                      disabled={!mayPublish || busy === key || !item.answers_current_source}
                      onClick={() => void act(item, 'approve')}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={!mayPublish || busy === key}
                      onClick={() => void act(item, 'reject')}
                    >
                      Reject
                    </button>
                  </>
                ) : null}
                <button
                  type="button"
                  disabled={!mayPublish || busy === key}
                  onClick={() => {
                    setEditing(key)
                    setDraft(item.candidate_value)
                  }}
                >
                  {item.status === 'rejected' ? 'Write the translation' : 'Edit'}
                </button>
              </div>
            )}
          </article>
        )
      })}

      {queue && queue.items.length === 0 ? (
        <p className="ops-sub">
          {state === 'rejected'
            ? 'Nothing has been rejected.'
            : 'Nothing is waiting for a decision.'}
        </p>
      ) : null}
    </div>
  )
}

function Insight() {
  const [funnel, setFunnel] = useState<FunnelView | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void fetchFunnel()
      .then(setFunnel)
      .catch((problem) =>
        setError(problem instanceof Error ? problem.message : 'Unavailable'),
      )
  }, [])

  if (error) return <p className="ops-error">{error}</p>
  if (!funnel) return <p className="ops-sub">Loading…</p>

  const { totals, assistant, search_health: search, cost } = funnel
  const stages: Array<[string, number]> = [
    ['Saw an experience', totals.experience_impression],
    ['Opened one', totals.experience_viewed],
    ['Added to trip', totals.cart_item_added],
    ['Started checkout', totals.checkout_started],
    ['Booked', totals.booking_completed],
  ]
  const top = Math.max(1, totals.experience_impression)
  const rate = (value: number | null) =>
    value == null ? '—' : `${(value * 100).toFixed(1)}%`

  // The assistant costs money and roughly twelve seconds a turn. Whether it
  // earns that is one comparison, so it goes at the top rather than buried.
  const lift =
    assistant.touched_conversion != null && assistant.untouched_conversion != null
      ? assistant.touched_conversion - assistant.untouched_conversion
      : null

  return (
    <div className="ops-insight">
      <div className="ops-metrics">
        <div>
          <span className="ops-sub">Assistant lift</span>
          <strong>
            {lift == null ? 'Not enough data' : `${lift >= 0 ? '+' : ''}${(lift * 100).toFixed(1)}pp`}
          </strong>
          <span className="ops-sub">
            {rate(assistant.touched_conversion)} with · {rate(assistant.untouched_conversion)}{' '}
            without · {assistant.touched_sessions + assistant.untouched_sessions} sessions
          </span>
        </div>
        <div>
          <span className="ops-sub">Searches finding nothing</span>
          <strong>{rate(search.zero_result_rate)}</strong>
          <span className="ops-sub">
            {search.zero_results} of {search.searches} · {search.relaxed_recoveries} recovered by
            relaxing
          </span>
        </div>
        <div>
          <span className="ops-sub">Model spend today ({cost.day})</span>
          <strong>
            ${cost.spent_usd.toFixed(2)} / ${cost.budget_usd.toFixed(2)}
          </strong>
          <span className="ops-sub">{cost.calls} calls</span>
          {cost.breaker_tripped ? (
            <span className="ops-flag ops-flag-off">budget breaker tripped</span>
          ) : null}
        </div>
      </div>
      <ul className="ops-funnel">
        {stages.map(([label, value]) => (
          <li key={label}>
            <span>{label}</span>
            <div className="ops-bar">
              <div style={{ width: `${Math.min(100, (value / top) * 100)}%` }} />
            </div>
            <strong>{value.toLocaleString()}</strong>
          </li>
        ))}
      </ul>
      <p className="ops-sub">
        A zero-result rate that will not come down is usually a catalogue gap,
        not a ranking bug. Those queries are the buying list.
      </p>
    </div>
  )
}

export default function AdminConsole() {
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [checking, setChecking] = useState(Boolean(getApiKey()))
  const [tab, setTab] = useState<Tab>('review')
  const [page, setPage] = useState<CatalogPage | null>(null)
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [selected, setSelected] = useState<CatalogRow | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [incompleteOnly, setIncompleteOnly] = useState(false)
  const [pageNumber, setPageNumber] = useState(1)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ceiling, setCeiling] = useState(1.5)

  useEffect(() => {
    if (!getApiKey()) return
    void whoami()
      .then(setPrincipal)
      .catch(() => setApiKey(''))
      .finally(() => setChecking(false))
  }, [])

  const can = useCallback(
    (capability: Capability) =>
      Boolean(principal?.capabilities.includes(capability)),
    [principal],
  )

  const filters = useMemo<CatalogFilters>(
    () => ({
      q: query || undefined,
      status: statusFilter || undefined,
      needsReview: tab === 'review' ? true : undefined,
      incomplete: tab === 'catalog' && incompleteOnly ? true : undefined,
      page: pageNumber,
      pageSize: 25,
    }),
    [query, statusFilter, tab, pageNumber, incompleteOnly],
  )

  const load = useCallback(async () => {
    if (!principal) return
    setLoadError(null)
    try {
      const [catalog, overview] = await Promise.all([
        fetchCatalog(filters),
        fetchOverview(),
      ])
      setPage(catalog)
      setSummary(overview)
    } catch (problem) {
      setLoadError(problem instanceof Error ? problem.message : 'Could not load')
    }
  }, [principal, filters])

  useEffect(() => {
    if (tab === 'review' || tab === 'catalog') void load()
  }, [tab, load])

  useEffect(() => {
    if (!principal) return
    void fetchSettings()
      .then((payload) => {
        const found = payload.settings.find(
          (setting) => setting.key === 'max_merchandising_boost',
        )
        if (typeof found?.value === 'number') setCeiling(found.value)
      })
      .catch(() => undefined)
  }, [principal])

  const applyRow = (row: CatalogRow) => {
    setSelected(row)
    setPage((current) =>
      current
        ? {
            ...current,
            items: current.items.map((item) => (item.id === row.id ? row : item)),
          }
        : current,
    )
    void fetchOverview().then(setSummary).catch(() => undefined)
  }

  if (checking) return <div className="ops-shell ops-sub">Checking your key…</div>
  if (!principal)
    return (
      <SignIn
        onSignedIn={(who) => {
          setPrincipal(who)
          setChecking(false)
        }}
      />
    )

  const visibleTabs = TABS.filter((entry) => !entry.needs || can(entry.needs))

  return (
    <div className="ops-shell">
      <header className="ops-header">
        <div>
          <h1>Vietra operations</h1>
          <span className="ops-sub">
            {principal.name || principal.email} · {principal.role.replace('_', ' ')}
          </span>
        </div>
        <div className="ops-header-right">
          {summary ? (
            <span className={summary.needs_review ? 'ops-flag' : 'ops-sub'}>
              {summary.needs_review} awaiting review
            </span>
          ) : null}
          <a href="/">Storefront</a>
          <button
            type="button"
            onClick={() => {
              setApiKey('')
              setPrincipal(null)
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <nav className="ops-tabs">
        {visibleTabs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className={tab === entry.id ? 'is-current' : ''}
            onClick={() => {
              setTab(entry.id)
              setPageNumber(1)
              setSelected(null)
            }}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      <main className="ops-main">
        {tab === 'translations' ? <Translations can={can} /> : null}
        {tab === 'settings' ? <Settings can={can} /> : null}
        {tab === 'audit' ? <Audit /> : null}
        {tab === 'insight' ? <Insight /> : null}

        {tab === 'review' || tab === 'catalog' ? (
          <div className="ops-catalog">
            <div className="ops-list">
              {tab === 'review' ? (
                <p className="ops-sub">
                  Imported records the system was not confident about. They are
                  held out of search until someone here decides, so an unreviewed
                  listing never reaches a shopper.
                </p>
              ) : (
                <div className="ops-filters">
                  <input
                    value={query}
                    placeholder="Search title, slug or destination"
                    onChange={(event) => {
                      setQuery(event.target.value)
                      setPageNumber(1)
                    }}
                  />
                  <select
                    value={statusFilter}
                    onChange={(event) => {
                      setStatusFilter(event.target.value)
                      setPageNumber(1)
                    }}
                  >
                    <option value="">Any status</option>
                    {STATUSES.map((status) => (
                      <option key={status} value={status}>
                        {status.replace('_', ' ').toLowerCase()}
                      </option>
                    ))}
                  </select>
                  {/* The gate guards the transition, so anything that went
                      live before it existed is still live and still broken.
                      Production's three description-less listings were found
                      by a script that read all 379 records one at a time. */}
                  <label className="ops-check">
                    <input
                      type="checkbox"
                      checked={incompleteOnly}
                      onChange={(event) => {
                        setIncompleteOnly(event.target.checked)
                        setPageNumber(1)
                      }}
                    />
                    {/* Named for what it actually queries. The filter is a
                        single SQL predicate over columns and EXISTS, so it
                        finds listings missing required content - it cannot see
                        a stale or placeholder search document, because both of
                        those need a fingerprint recomputed per record. Calling
                        it "would not publish today" implied the gate's full
                        verdict and quietly left index failures off the
                        worklist. */}
                    <span>Missing required content</span>
                  </label>
                </div>
              )}

              {loadError ? <p className="ops-error">{loadError}</p> : null}

              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Experience</th>
                    <th>Status</th>
                    <th>From</th>
                    <th>Rating</th>
                    <th>Merchandising</th>
                    <th>Source</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(page?.items ?? []).map((row) => (
                    <Row
                      key={row.id}
                      row={row}
                      can={can}
                      selected={selected?.id === row.id}
                      onSelect={() => setSelected(row)}
                    />
                  ))}
                  {page && page.items.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="ops-sub">
                        {tab === 'review'
                          ? 'Nothing is waiting. Every imported experience has been decided.'
                          : 'No experiences match those filters.'}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>

              {page && page.total > page.page_size ? (
                <div className="ops-pager">
                  <button
                    type="button"
                    disabled={pageNumber <= 1}
                    onClick={() => setPageNumber((value) => value - 1)}
                  >
                    Previous
                  </button>
                  <span className="ops-sub">
                    {(page.page - 1) * page.page_size + 1}–
                    {Math.min(page.page * page.page_size, page.total)} of {page.total}
                  </span>
                  <button
                    type="button"
                    disabled={page.page * page.page_size >= page.total}
                    onClick={() => setPageNumber((value) => value + 1)}
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </div>

            {selected ? (
              <Editor
                row={selected}
                can={can}
                ceiling={ceiling}
                onChanged={applyRow}
                onClose={() => setSelected(null)}
              />
            ) : null}
          </div>
        ) : null}
      </main>
    </div>
  )
}
