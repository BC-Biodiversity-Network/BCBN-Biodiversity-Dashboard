import { useEffect, useRef, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, searchableLength } from './api'
import { NO_FILTERS, SORTS, activeFilterCount, shownSort } from './route'

// The sort choices, in words. Relevance is only offered with a search.
const SORT_LABELS = {
  relevance: 'Relevance',
  newest: 'Newest first',
  oldest: 'Oldest first',
  title: 'Title A-Z',
}

// The access rights a record can give, in words, in the API's order.
const ACCESS_LABELS = { open: 'Open access', restricted: 'Restricted access' }

// One section of the Filters panel, with its heading.
function FilterSection({ title, children }) {
  return (
    <section className="datasets-filter-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

// One checkbox with its label. The panel shows no counts: its choices only
// take effect on Apply, so counts next to them would be misleading.
function Checkbox({ label, checked, onChange }) {
  return (
    <label className="datasets-filter-option">
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span>{label}</span>
    </label>
  )
}

// Orders catalogue options by their readable name, A to Z, ignoring case
// and accents.
function byName(a, b) {
  return (a.name ?? a.value).localeCompare(b.name ?? b.value, 'en', { sensitivity: 'base' })
}

// A list with value taken out if it is in it, or added if it is not.
function toggled(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

// The panel's pending choices, from the applied filters: the years and the
// organism as the text typed in their boxes.
function draftFrom(state) {
  return {
    catalogues: state.catalogues,
    yearFrom: state.yearFrom == null ? '' : String(state.yearFrom),
    yearTo: state.yearTo == null ? '' : String(state.yearTo),
    organism: state.organism,
    access: state.access,
    mappable: state.mappable,
  }
}

// Reads a year typed in a box: a whole number, or null when the box is empty.
// Returns undefined for anything else.
function typedYear(text) {
  if (!text.trim()) {
    return null
  }
  return /^\d{4}$/.test(text.trim()) ? Number(text.trim()) : undefined
}

// Checks the pending choices and turns them into filters for the page.
// Returns { filters } or { problems }, problems by section (published,
// organism).
export function checkDraft(draft) {
  const problems = {}
  const yearFrom = typedYear(draft.yearFrom)
  const yearTo = typedYear(draft.yearTo)
  if (yearFrom === undefined || yearTo === undefined) {
    problems.published = 'Type a year with four digits, or leave the box empty.'
  } else if (yearFrom != null && yearTo != null && yearFrom > yearTo) {
    problems.published = 'The first year must not be after the second.'
  }
  const organism = draft.organism.trim()
  if (organism && searchableLength(organism) < MIN_SEARCH_CHARACTERS) {
    problems.organism = `Type at least ${MIN_SEARCH_CHARACTERS} letters or digits.`
  }
  if (Object.keys(problems).length > 0) {
    return { problems }
  }
  return {
    filters: { catalogues: draft.catalogues, yearFrom, yearTo, organism, access: draft.access, mappable: draft.mappable },
  }
}

// True if two sets of filters are the same (the order of ticked values does
// not matter).
function sameFilters(a, b) {
  const sorted = (list) => [...list].sort().join('\n')
  return sorted(a.catalogues) === sorted(b.catalogues) && a.yearFrom === b.yearFrom && a.yearTo === b.yearTo
    && a.organism === b.organism && sorted(a.access) === sorted(b.access) && a.mappable === b.mappable
}

// The Filters panel. Every change in it is only pending until Apply (or
// Enter in a text box) sends them all at once and closes it; Cancel, the
// Filters button or Escape closes it and drops them. The panel is only
// mounted while open, so it always starts from the applied filters (state).
// options are the API's answer for the applied filters: only the
// catalogues that have datasets for them are offered (plus any ticked), and
// the Published note gives their years.
function FilterPanel({ state, options, onApply, onClose }) {
  const [draft, setDraft] = useState(() => draftFrom(state))
  const [problems, setProblems] = useState({})
  const years = options.published?.years ?? []
  const first = years[0]?.year
  const last = years[years.length - 1]?.year
  const listedCatalogues = options.catalogue
    .filter((option) => option.count > 0 || draft.catalogues.includes(option.value))
    .sort(byName)

  // Changes some of the pending choices.
  function change(changes) {
    setDraft((old) => ({ ...old, ...changes }))
  }

  // Checks the pending choices and applies them all, or shows what is wrong.
  function apply(event) {
    event.preventDefault()
    const checked = checkDraft(draft)
    if (checked.problems) {
      setProblems(checked.problems)
      return
    }
    onApply(sameFilters(checked.filters, state) ? null : checked.filters)
  }

  return (
    <form id="datasets-filter-panel" className="datasets-filter-panel" onSubmit={apply} aria-label="Filters">
      <FilterSection title="Source catalogue">
        <fieldset className="datasets-filter-options">
          <legend className="visually-hidden">Source catalogue</legend>
          {listedCatalogues.map((option) => (
            <Checkbox
              key={option.value}
              label={option.name}
              checked={draft.catalogues.includes(option.value)}
              onChange={() => change({ catalogues: toggled(draft.catalogues, option.value) })}
            />
          ))}
        </fieldset>
      </FilterSection>

      <FilterSection title="Published">
        <div className="datasets-year-form">
          <label>
            From
            <input type="text" inputMode="numeric" value={draft.yearFrom} placeholder={first ? String(first) : ''}
              onChange={(event) => change({ yearFrom: event.target.value })} size={5} />
          </label>
          <label>
            to
            <input type="text" inputMode="numeric" value={draft.yearTo} placeholder={last ? String(last) : ''}
              onChange={(event) => change({ yearTo: event.target.value })} size={5} />
          </label>
        </div>
        {problems.published && <p className="datasets-error">{problems.published}</p>}
        <p className="datasets-filter-note">
          When the record was published, not when the data were collected (the records have no
          collection dates).{first && ` These datasets were published from ${first} to ${last}.`}
          {options.published?.no_date > 0
            && ` ${options.published.no_date.toLocaleString()} have no date and are left out when a range is set.`}
        </p>
      </FilterSection>

      <FilterSection title="Organisms">
        <label htmlFor="datasets-organism" className="visually-hidden">Organism name</label>
        <input id="datasets-organism" className="datasets-organism-input" type="text" value={draft.organism}
          placeholder="For example salmon or Oncorhynchus" onChange={(event) => change({ organism: event.target.value })} />
        {problems.organism && <p className="datasets-error">{problems.organism}</p>}
        <p className="datasets-filter-note">
          Names as written in the record; common and scientific names are not linked yet.
        </p>
      </FilterSection>

      <FilterSection title="Access">
        {options.access.map((option) => (
          <Checkbox
            key={option.value}
            label={ACCESS_LABELS[option.value] ?? option.value}
            checked={draft.access.includes(option.value)}
            onChange={() => change({ access: toggled(draft.access, option.value) })}
          />
        ))}
      </FilterSection>

      <FilterSection title="Location">
        <Checkbox
          label="Only datasets that can be shown on the map"
          checked={draft.mappable}
          onChange={() => change({ mappable: !draft.mappable })}
        />
      </FilterSection>

      <div className="datasets-filter-actions">
        <button type="submit" className="datasets-apply-button">Apply</button>
        <button type="button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  )
}

// The row under the search box: the sort, the Filters button with how many
// filters are applied, and "Clear all"; and under it the Filters panel when
// it is open. state is the page's state (see route.js), options the API's
// counts for it (null until the first answer), and onChange(changes)
// changes the sort or the filters (the page goes back to page 1).
export default function SortAndFilters({ state, options, onChange }) {
  const [open, setOpen] = useState(false)
  const buttonRef = useRef(null)
  const searching = Boolean(state.q.trim())
  const sort = shownSort(state)
  const active = activeFilterCount(state)

  // Closes the panel, dropping any pending changes, and puts the focus back
  // on the Filters button.
  function close() {
    setOpen(false)
    buttonRef.current?.focus()
  }

  // Escape closes the panel too, wherever the focus is.
  useEffect(() => {
    if (!open) {
      return undefined
    }
    // Closes the panel on Escape.
    function onKeyDown(event) {
      if (event.key === 'Escape') {
        close()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  // Keeps the default order out of the address, so it changes with the search.
  function changeSort(value) {
    onChange({ sort: value === (searching ? 'relevance' : 'newest') ? '' : value })
  }

  // Applies the panel's choices (null when nothing changed) and closes it.
  function apply(filters) {
    if (filters) {
      onChange(filters)
    }
    close()
  }

  // Clears every applied filter at once; an open panel closes, dropping
  // its pending changes.
  function clearAll() {
    setOpen(false)
    onChange(NO_FILTERS)
  }

  return (
    <>
      <div className="datasets-controls">
        <label className="datasets-sort">
          Sort by
          <select value={sort} onChange={(event) => changeSort(event.target.value)}>
            {SORTS.filter((value) => value !== 'relevance' || searching).map((value) => (
              <option key={value} value={value}>{SORT_LABELS[value]}</option>
            ))}
          </select>
        </label>
        <button
          ref={buttonRef}
          type="button"
          className={open ? 'datasets-filters-button open' : 'datasets-filters-button'}
          aria-expanded={open}
          aria-controls="datasets-filter-panel"
          onClick={() => (open ? close() : setOpen(true))}
        >
          Filters{active > 0 && ` (${active})`}
        </button>
        {active > 0 && (
          <button type="button" className="text-button" onClick={clearAll}>Clear all</button>
        )}
      </div>

      {/* Keyed by the applied filters, so if they change while the panel is
          open (the back button), it starts again from them. */}
      {open && options && (
        <FilterPanel key={JSON.stringify(draftFrom(state))} state={state} options={options} onApply={apply}
          onClose={close} />
      )}
    </>
  )
}
