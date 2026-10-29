import { useEffect, useMemo, useRef, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, fetchDataset, fetchDatasets, searchableLength } from './api'
import DatasetDetail from './DatasetDetail'
import DatasetMap from './DatasetMap'
import DatasetsFooter from './DatasetsFooter'
import { describeLocation, issuedLabel, licenceListLabel, plainText } from './datasetText'
import { datasetsSearch, readDatasetsState } from './route'
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
// button clicked, and says so when a search is too short to send.
function SearchForm({ initial, onSearch }) {
  const [text, setText] = useState(initial)
  const [tooShort, setTooShort] = useState(false)

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
        placeholder="Search datasets, for example eagle"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <button type="submit" className="datasets-search-button">Search</button>
      {tooShort && (
        <p className="datasets-note">Type at least {MIN_SEARCH_CHARACTERS} letters or digits.</p>
      )}
    </form>
  )
}

// The source catalogue filter, folded under its name (open at first): one
// checkbox per catalogue with its readable name and how many datasets it has
// for the search, most first (the API's order), in a box that scrolls when
// the list is long. options come from the API ({ value, count, name }).
// Catalogues with no datasets for the search are left out unless ticked.
function CatalogueFilter({ options, selected, onChange }) {
  const listed = options.filter((option) => option.count > 0 || selected.includes(option.value))

  // Ticks or unticks one value.
  function toggle(value) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  }

  return (
    <details className="datasets-filter" open>
      <summary>
        Source catalogue ({listed.length}){selected.length > 0 && `, ${selected.length} selected`}
      </summary>
      <fieldset className="datasets-filter-options">
        <legend className="visually-hidden">Source catalogue</legend>
        {listed.map((option) => (
          <label key={option.value} className="datasets-filter-option">
            <input
              type="checkbox"
              checked={selected.includes(option.value)}
              onChange={() => toggle(option.value)}
            />
            <span>{option.name}</span>
            <span className="datasets-count">{option.count.toLocaleString()}</span>
          </label>
        ))}
      </fieldset>
    </details>
  )
}

// One dataset in the list: its title (a link that opens its details), its
// catalogue, issued date and licence, the start of its abstract, and the
// "May include BC" tag for a dataset that is not BC data.
function ResultItem({ result, href, onOpen }) {
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
        {issued && <> · Issued {issued}</>}
        {' · '}{licenceListLabel(result.licences)}
      </p>
      <MayIncludeBcTag frontendGroup={result.frontend_group} />
      {result.abstract_preview && <p className="dataset-preview">{plainText(result.abstract_preview)}</p>}
    </li>
  )
}

// Previous and next page buttons, with where we are.
function Paging({ page, pages, onPage }) {
  if (pages <= 1) {
    return null
  }
  return (
    <nav className="datasets-paging" aria-label="Pages of results">
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>Page {page} of {pages}</span>
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  )
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
  } else if (location && location.boxes.length + location.points.length + location.places.length === 0) {
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
                <DatasetDetail dataset={detail.data} location={location} onBack={() => go({ dataset: null })} />
              )}
            </>
          ) : (
            <>
              <h1 className="datasets-heading">Datasets about BC</h1>
              <p className="datasets-intro">
                Datasets from Canadian research and government catalogues, gathered by Lunaris. Find one
                and follow its link to view and download it on the original site.
              </p>
              <SearchForm key={state.q} initial={state.q} onSearch={(q) => filter({ q })} />
              <WiderSwitch checked={state.wider} onChange={(wider) => filter({ wider })} />

              {list.error && <p className="datasets-error">{errorText(list.error)}</p>}
              {data && (
                <>
                  <div className="datasets-filters">
                    <CatalogueFilter
                      options={options.catalogue}
                      selected={state.catalogues}
                      onChange={(catalogues) => filter({ catalogues })}
                    />
                    {state.catalogues.length > 0 && (
                      <button type="button" className="text-button" onClick={() => filter({ catalogues: [] })}>
                        Clear the filter
                      </button>
                    )}
                  </div>
                  <p className="datasets-total" aria-live="polite">
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
                      />
                    ))}
                  </ol>
                  <Paging page={state.page} pages={data.pages} onPage={(page) => go({ page })} />

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
