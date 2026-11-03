import { useEffect, useMemo, useRef, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, fetchDataset, fetchDatasets, searchWords, searchableLength } from './api'
import DatasetDetail from './DatasetDetail'
import SortAndFilters from './DatasetFilters'
import DatasetMap from './DatasetMap'
import DatasetsFooter from './DatasetsFooter'
import { describeLocation, issuedLabel, licenceListLabel, plainText } from './datasetText'
import { pageItems } from './paging'
import { datasetsSearch, readDatasetsState, speciesMapSearch } from './route'
import SpeciesName, { speciesLabel } from './SpeciesName'
import { MayIncludeBcTag, SHOW_WIDER_SWITCH, WiderSwitch } from './widerDatasets'

// What to say when a request to the API fails, by the kind of failure (see
// ApiError in api.js).
function errorText(error) {
  if (error.kind === 'unreachable') {
    return 'The datasets could not be loaded: the site\'s API did not answer. Please try again later.'
  }
  if (error.kind === 'rejected') {
    return error.message
  }
  return `The datasets could not be loaded: ${error.message}`
}

// The keyword search box. It sends the search when Enter is pressed or the
// button clicked, and says so when a search is too short to send. Emptying
// the box (deleting the text, or the browser's clear button) removes the
// applied search at once, without Enter. initial is the applied search: when
// it changes from outside (the search chip, the back button), the box shows it.
function SearchForm({ initial, onSearch }) {
  const [text, setText] = useState(initial)
  const [tooShort, setTooShort] = useState(false)

  useEffect(() => {
    setText(initial)
    setTooShort(false)
  }, [initial])

  // Keeps what is typed; an empty box removes the applied search, if any.
  function change(value) {
    setText(value)
    if (!value.trim()) {
      setTooShort(false)
      if (initial.trim()) {
        onSearch('')
      }
    }
  }

  // Sends the search, unless it has text that is too short.
  function submit(event) {
    event.preventDefault()
    if (text.trim() && searchableLength(text) < MIN_SEARCH_CHARACTERS) {
      setTooShort(true)
      return
    }
    setTooShort(false)
    onSearch(text)
  }

  return (
    <form className="datasets-search" role="search" onSubmit={submit}>
      <label htmlFor="datasets-query" className="visually-hidden">Search datasets</label>
      <input
        id="datasets-query"
        className="search-input"
        type="search"
        placeholder="Search titles, abstracts and keywords, for example wetland or Fraser River"
        value={text}
        onChange={(event) => change(event.target.value)}
      />
      <button type="submit" className="datasets-search-button">Search</button>
      {tooShort && (
        <p className="datasets-note">Type at least {MIN_SEARCH_CHARACTERS} letters or digits.</p>
      )}
    </form>
  )
}

// One dataset in the list: its title (a link that opens its details), its
// catalogue, published date (the record's issued date) and licence, the
// start of its abstract, and the "May include BC" tag for a dataset that is
// not BC data. With
// showOrganisms (while the Organisms filter is in use), also the organism
// names stored for it, so it is clear why it matched.
function ResultItem({ result, href, onOpen, showOrganisms }) {
  const issued = issuedLabel(result.issued)
  return (
    <li className="dataset-item">
      <a
        className="dataset-item-title"
        href={href}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
            return
          }
          event.preventDefault()
          onOpen(result.id)
        }}
      >
        {plainText(result.title) || 'Untitled dataset'}
      </a>
      <p className="dataset-meta">
        {result.source_name}
        {issued && <> · Published {issued}</>}
        {' · '}{licenceListLabel(result.licences)}
      </p>
      <MayIncludeBcTag frontendGroup={result.frontend_group} />
      {showOrganisms && result.organisms?.length > 0 && (
        <p className="dataset-meta">Organisms: {result.organisms.join(', ')}</p>
      )}
      {result.abstract_preview && <p className="dataset-preview">{plainText(result.abstract_preview)}</p>}
    </li>
  )
}

// One word of the applied search, as a chip that removes it: Search: "eagle" ×.
// The search needs every word on its own, so each word has its own chip.
function SearchChip({ word, onRemove }) {
  return (
    <span className="datasets-chip">
      <span>Search: “{word}”</span>
      <button type="button" className="datasets-chip-remove" onClick={onRemove}
        aria-label={`Remove ${word} from the search`}>
        ×
      </button>
    </span>
  )
}

// The species filter, as a chip that removes it: "Species: Sockeye Salmon
// (Oncorhynchus nerka) ×". named is the species as the API's answer names
// it (filters.species), or null while that answer is for another species
// or has not come; the key is shown then.
function SpeciesChip({ speciesKey, named, onRemove }) {
  const label = named ? speciesLabel(named) : speciesKey
  return (
    <span className="datasets-chip">
      <span>Species: {named ? <SpeciesName species={named} /> : speciesKey}</span>
      <button type="button" className="datasets-chip-remove" onClick={onRemove}
        aria-label={`Remove the species filter (${label})`}>
        ×
      </button>
    </span>
  )
}

// Below this width (in pixels) the page buttons show no pages on either side
// of the current one, so the row fits on one line: a phone, or a narrow
// window, where the list is narrow too.
const WIDE_PAGING_WIDTH = 420

// The width of an element, in pixels, kept up to date as it changes; null
// until it is known (and always in a browser without ResizeObserver).
function useWidth(ref) {
  const [width, setWidth] = useState(null)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') {
      return undefined
    }
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return width
}

// The page buttons: « Previous, the page numbers with "..." for the pages
// left out (see pageItems), Next ». The current page is highlighted and
// marked as the current one for screen readers. onPage(page) goes to a page.
function PageButtons({ page, pages, onPage }) {
  const ref = useRef(null)
  const width = useWidth(ref)
  const around = width !== null && width < WIDE_PAGING_WIDTH ? 0 : 1
  return (
    <nav className="datasets-paging" aria-label="Pages of results" ref={ref}>
      <button type="button" className="datasets-page-step" disabled={page <= 1} onClick={() => onPage(page - 1)}
        aria-label="Previous page">
        « Previous
      </button>
      {pageItems(page, pages, around).map((item, i) => (item === 'gap'
        ? <span key={`gap-${i}`} className="datasets-page-gap" aria-hidden="true">...</span>
        : (
          <button
            key={item}
            type="button"
            className={item === page ? 'datasets-page-number current' : 'datasets-page-number'}
            aria-current={item === page ? 'page' : undefined}
            aria-label={`Page ${item}`}
            onClick={() => item !== page && onPage(item)}
          >
            {item}
          </button>
        )))}
      <button type="button" className="datasets-page-step" disabled={page >= pages} onClick={() => onPage(page + 1)}
        aria-label="Next page">
        Next »
      </button>
    </nav>
  )
}

// The page buttons, or nothing when there is only one page.
function Paging({ page, pages, onPage }) {
  return pages > 1 ? <PageButtons page={page} pages={pages} onPage={onPage} /> : null
}

// Fetches one page of datasets for a state, again whenever the search, the
// filters or the page change. Returns { data, error, loading }; data stays
// the last answer while a new one loads, so the list does not flash empty.
function useDatasets(state) {
  const [answer, setAnswer] = useState({ data: null, error: null, loading: true })
  // The state without the selected dataset: opening a dataset does not
  // fetch the list again.
  const key = datasetsSearch({ ...state, dataset: null })

  useEffect(() => {
    const controller = new AbortController()
    setAnswer((old) => ({ ...old, loading: true }))
    fetchDatasets(readDatasetsState(key), { signal: controller.signal })
      .then((data) => setAnswer({ data, error: null, loading: false }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          setAnswer({ data: null, error, loading: false })
        }
      })
    return () => controller.abort()
  }, [key])

  return answer
}

// Fetches the selected dataset, or nothing when none is selected. Returns
// { data, error, loading }.
function useDataset(id) {
  const [answer, setAnswer] = useState({ data: null, error: null, loading: false })

  useEffect(() => {
    if (!id) {
      setAnswer({ data: null, error: null, loading: false })
      return undefined
    }
    const controller = new AbortController()
    setAnswer({ data: null, error: null, loading: true })
    fetchDataset(id, { signal: controller.signal })
      .then((data) => setAnswer({ data, error: null, loading: false }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          setAnswer({ data: null, error, loading: false })
        }
      })
    return () => controller.abort()
  }, [id])

  return answer
}

// The Datasets page: Lunaris datasets about BC, to find one and follow its
// link to the original site (we do not host the data). On the left the
// search, filters and list, or the selected dataset; on the right a map of
// the selected dataset only. Everything the page shows comes from the
// address (see route.js): search is the query string and navigate changes
// it, so links can be shared and the back button works.
export default function DatasetsPage({ search, navigate }) {
  const state = readDatasetsState(search)
  if (!SHOW_WIDER_SWITCH) {
    state.wider = false
  }
  const list = useDatasets(state)
  const detail = useDataset(state.dataset)
  const panelRef = useRef(null)
  const listTopRef = useRef(null)
  const location = useMemo(() => (detail.data ? describeLocation(detail.data) : null), [detail.data])

  // Goes to a new state of the page, as a new history entry. Changing the
  // search or a filter goes back to page 1.
  function go(changes) {
    navigate(datasetsSearch({ ...state, ...changes }))
  }
  // Changes the search or a filter: back to page 1, no dataset selected.
  function filter(changes) {
    go({ ...changes, page: 1, dataset: null })
  }
  // Applies a search, or removes it when q is empty. Relevance needs a
  // search, so removing it also drops a relevance sort (back to newest).
  function applySearch(q) {
    filter(!q.trim() && state.sort === 'relevance' ? { q, sort: '' } : { q })
  }

  // The words of the applied search, one chip each (see searchWords).
  const words = searchWords(state.q)
  // Removes one word from the search and searches again with the others, as
  // typed; what is left is no search when it is too short to send.
  function removeWord(word) {
    const left = words.filter((other) => other.word !== word)
    const length = left.reduce((sum, other) => sum + other.word.length, 0)
    applySearch(length >= MIN_SEARCH_CHARACTERS ? left.map((other) => other.typed).join(' ') : '')
  }

  // Goes to another page of the list, and back to the top of the list (the
  // result count), wherever the page or the panel was scrolled to.
  function goToPage(page) {
    go({ page })
    listTopRef.current?.scrollIntoView?.({ block: 'start' })
  }

  // Opens a dataset's details at the top of the panel.
  function openDataset(id) {
    go({ dataset: id })
    if (panelRef.current) {
      panelRef.current.scrollTop = 0
    }
  }

  const data = list.data
  const options = data?.options

  let mapMessage = null
  if (!state.dataset) {
    mapMessage = 'Select a dataset to see its location here.'
  } else if (detail.loading) {
    mapMessage = 'Loading the dataset...'
  } else if (location && location.boxes.length + location.matchingBoxes.length + location.points.length
      + location.places.length === 0) {
    mapMessage = 'Nothing to draw for this dataset. See Location for why.'
  }

  return (
    <div className="datasets-page">
      <div className="datasets-main">
        <div className="datasets-panel" ref={panelRef}>
          {state.dataset ? (
            <>
              {detail.loading && <p className="datasets-note">Loading the dataset...</p>}
              {detail.error && (
                <p className="datasets-error">
                  {detail.error.status === 404 ? 'This dataset is not on the site.' : errorText(detail.error)}
                </p>
              )}
              {(detail.error || detail.loading) && (
                <button type="button" className="text-button" onClick={() => go({ dataset: null })}>
                  Back to the list
                </button>
              )}
              {detail.data && (
                <DatasetDetail dataset={detail.data} location={location} onBack={() => go({ dataset: null })}
                  onOpenSpecies={(key) => navigate(speciesMapSearch(key))} />
              )}
            </>
          ) : (
            <>
              <h1 className="datasets-heading">Datasets about BC</h1>
              <p className="datasets-intro">
                Datasets from Canadian research and government catalogues, gathered by Lunaris. Find one
                and follow its link to view and download it on the original site.
              </p>
              <SearchForm initial={state.q} onSearch={applySearch} />
              <WiderSwitch checked={state.wider} onChange={(wider) => filter({ wider })} />
              <SortAndFilters state={state} options={options ?? null} onChange={filter} />
              {(words.length > 0 || state.species) && (
                <div className="datasets-chips">
                  {words.map(({ word, typed }) => (
                    <SearchChip key={word} word={typed} onRemove={() => removeWord(word)} />
                  ))}
                  {state.species && (
                    <SpeciesChip
                      speciesKey={state.species}
                      named={data?.filters?.species?.requested_key === state.species ? data.filters.species : null}
                      onRemove={() => filter({ species: '' })}
                    />
                  )}
                </div>
              )}

              {list.error && <p className="datasets-error">{errorText(list.error)}</p>}
              {data && (
                <>
                  <p className="datasets-total" aria-live="polite" ref={listTopRef}>
                    {data.total === 0
                      ? 'No datasets match.'
                      : `${data.total.toLocaleString()} ${data.total === 1 ? 'dataset' : 'datasets'}`}
                    {list.loading && ' (updating...)'}
                  </p>
                  <ol className="dataset-list">
                    {data.results.map((result) => (
                      <ResultItem
                        key={result.id}
                        result={result}
                        href={datasetsSearch({ ...state, dataset: result.id })}
                        onOpen={openDataset}
                        showOrganisms={Boolean(state.organism)}
                      />
                    ))}
                  </ol>
                  <Paging page={state.page} pages={data.pages} onPage={goToPage} />

                </>
              )}
              {!data && list.loading && <p className="datasets-note">Loading datasets...</p>}
            </>
          )}
        </div>
        <DatasetMap
          location={location}
          datasetId={detail.data?.id ?? null}
          sourceName={detail.data?.source_name ?? ''}
          message={mapMessage}
        />
      </div>
      <DatasetsFooter />
    </div>
  )
}
