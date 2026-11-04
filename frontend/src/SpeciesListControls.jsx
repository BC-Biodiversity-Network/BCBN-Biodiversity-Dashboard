import { useEffect, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, searchableLength } from './api'
import Paging from './PageButtons'
import { LIST_STATUSES } from './route'

// The controls of a species list, on a rank card and in a hexagon's panel,
// which work the same way: the API filters, sorts and counts (see
// backend/api/species_list.py), and the options live in the address (see
// readList in route.js), so a filtered list can be shared.

// How long to wait after the last key press in the filter box before
// filtering, in milliseconds (the same as the search box).
const FILTER_DELAY_MS = 250

// What each order is called in the list's menu.
const SORT_LABELS = {
  most_records: 'Most records first',
  fewest_records: 'Fewest records first',
  scientific: 'Scientific name, A to Z',
  english: 'English name, A to Z',
}

// What each status option is called, and the chip class of a shown status.
const STATUS_LABELS = { red: 'Red', blue: 'Blue', yellow: 'Yellow', exotic: 'Exotic', none: 'No status' }
const STATUS_CHIPS = { red: 'status-red', blue: 'status-blue', yellow: 'status-yellow', exotic: 'status-exotic' }

// Whether a list has a filter on (a name or a status).
export function isFiltered(list) {
  return Boolean(list.q) || list.status.length > 0
}

// The filter box, the order and the BC status filter of a species list.
// list is its options ({ q, sort, status, page }), data the API's last
// answer for it (for the status counts; null while the first loads), and
// onChange(list) applies new options, back on the first page. filterLabel
// names the filter box for screen readers.
export default function SpeciesListControls({ list, data, filterLabel, onChange }) {
  const [typed, setTyped] = useState(list.q)

  // Filters a moment after the user stops typing; a filter too short to
  // search is not used.
  useEffect(() => {
    const usable = searchableLength(typed) >= MIN_SEARCH_CHARACTERS ? typed.trim() : ''
    if (usable === list.q) {
      return undefined
    }
    const timer = setTimeout(() => onChange({ ...list, q: usable, page: 1 }), FILTER_DELAY_MS)
    return () => clearTimeout(timer)
    // Only typing starts a new wait; list and onChange are read as they are
    // when it ends.
  }, [typed])

  // Shows the filter of an address reached another way (the back button, a
  // link), unless what is typed already stands for it.
  useEffect(() => {
    setTyped((text) => (text.trim() === list.q ? text : list.q))
  }, [list.q])

  const tooShort = typed.trim() !== '' && searchableLength(typed) < MIN_SEARCH_CHARACTERS

  // Ticks or unticks one status, keeping the statuses in their usual order.
  function toggleStatus(status) {
    const ticked = !list.status.includes(status)
    const chosen = LIST_STATUSES.filter((other) => (other === status ? ticked : list.status.includes(other)))
    onChange({ ...list, status: chosen, page: 1 })
  }

  return (
    <div className="species-list-controls">
      <input
        type="search"
        className="hexagon-filter"
        placeholder="Filter the species by name"
        aria-label={filterLabel}
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
      />
      {tooShort && <p className="hexagon-note">Type at least {MIN_SEARCH_CHARACTERS} letters or digits to filter.</p>}
      <label className="species-list-sort">
        Sort{' '}
        <select value={list.sort} onChange={(event) => onChange({ ...list, sort: event.target.value, page: 1 })}>
          {Object.entries(SORT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <fieldset className="species-list-statuses">
        <legend>BC status</legend>
        <div className="species-list-boxes">
          {LIST_STATUSES.map((status) => (
            <label key={status} className="species-list-box">
              <input type="checkbox" checked={list.status.includes(status)} onChange={() => toggleStatus(status)} />
              <span className={STATUS_CHIPS[status] ? `status-chip ${STATUS_CHIPS[status]}` : 'species-list-none'}>
                {STATUS_LABELS[status]}
              </span>
              {data?.status_counts && (
                <span className="species-list-count"> {data.status_counts[status].toLocaleString()}</span>
              )}
            </label>
          ))}
        </div>
        <p className="species-list-note">A species counts if it, or any subspecies or population of it, has the status.</p>
      </fieldset>
    </div>
  )
}

// The line above a species list saying which species it shows, when that
// is not plain: "12 species match", or "51 to 100 of 2,073 species" over
// more than one page ("... that match" with a filter on); null otherwise.
export function shownText(data, list) {
  const filtered = isFiltered(list)
  if (data.matching <= data.limit) {
    return filtered ? `${data.matching.toLocaleString()} species match.` : null
  }
  const first = data.offset + 1
  const last = Math.min(data.offset + data.limit, data.matching)
  return `${first.toLocaleString()} to ${last.toLocaleString()} of ${data.matching.toLocaleString()} species`
    + `${filtered ? ' that match' : ''}`
}

// What to say when no species passes the filters.
export function noMatchText(list) {
  if (list.q && list.status.length > 0) {
    return `No species here match “${list.q}” with the statuses chosen.`
  }
  return list.q ? `No species here match “${list.q}”.` : 'No species here have the statuses chosen.'
}

// The numbered page buttons under a species list (the Datasets page's), for
// data's matching species, limit at a time; onChange(list) goes to a page.
// A page past the end (the years changed, say) goes back to the first.
export function SpeciesListPaging({ data, list, onChange }) {
  const pages = Math.max(1, Math.ceil(data.matching / data.limit))
  const pastTheEnd = list.page > pages

  useEffect(() => {
    if (pastTheEnd) {
      onChange({ ...list, page: 1 })
    }
    // Only a page past the end matters.
  }, [pastTheEnd])

  return <Paging page={list.page} pages={pages} onPage={(page) => onChange({ ...list, page })} />
}
