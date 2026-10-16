import { useEffect, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, searchSpecies, searchableLength } from './api'

// How long to wait after the last key press before searching, in
// milliseconds. Long enough that typing "robin" sends one request, not five;
// short enough that the list still feels immediate.
const SEARCH_DELAY_MS = 250

// How many results to ask the API for.
const RESULT_LIMIT = 20

// The id of the results list, so the input can point screen readers at it.
const LIST_ID = 'species-search-results'

// The fields of display_names that are names from a BCSEE entry rather than
// the species' own names, and how each is labelled in the list.
const ENTRY_NAME_LABELS = {
  bcsee_entry_english_name: 'Common name of a BCSEE entry',
  bcsee_scientific_label: 'Scientific name from BCSEE',
}

// The search box: a text field with a list of matching species under it.
// Calls onSelect(result) when a species is picked, with the result as the API
// sent it (its specieskey is always the kept key; merged_from says which old
// key was typed, if any).
export default function SearchBox({ onSelect }) {
  const [query, setQuery] = useState('')
  // What the box is doing: 'idle' (empty), 'too_short', 'loading', 'results',
  // 'empty' (nothing found) or 'error'.
  const [status, setStatus] = useState('idle')
  const [results, setResults] = useState([])
  const [errorMessage, setErrorMessage] = useState('')
  const [activeIndex, setActiveIndex] = useState(-1)
  const [listOpen, setListOpen] = useState(false)

  // Searches a moment after the user stops typing. Each new key press cancels
  // the waiting search, and the request of an older search if it was already
  // sent, so a slow answer to "rob" can never replace the answer to "robin".
  useEffect(() => {
    if (query.trim() === '') {
      setStatus('idle')
      setResults([])
      return undefined
    }
    if (searchableLength(query) < MIN_SEARCH_CHARACTERS) {
      setStatus('too_short')
      setResults([])
      return undefined
    }

    const controller = new AbortController()
    const timer = setTimeout(() => {
      setStatus('loading')
      searchSpecies(query, { limit: RESULT_LIMIT, signal: controller.signal })
        .then((answer) => {
          // An answer to a search that has since been replaced is ignored.
          // fetch already stops a cancelled request, so this is a second
          // guard in case an answer still arrives.
          if (controller.signal.aborted) {
            return
          }
          setResults(answer.results)
          setActiveIndex(answer.results.length > 0 ? 0 : -1)
          setStatus(answer.results.length > 0 ? 'results' : 'empty')
        })
        .catch((error) => {
          if (error.name === 'AbortError') {
            return
          }
          console.error(error)
          setResults([])
          setErrorMessage(
            error.kind === 'unreachable'
              ? 'Species search is not available: the API could not be reached.'
              : error.message,
          )
          setStatus('error')
        })
    }, SEARCH_DELAY_MS)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  // Picks one result: tells the page, and closes the list.
  function choose(result) {
    onSelect(result)
    setListOpen(false)
  }

  // Keyboard use: the arrow keys move through the results, Enter picks the
  // highlighted one, Escape closes the list.
  function handleKeyDown(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (results.length === 0) {
        return
      }
      event.preventDefault()
      setListOpen(true)
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex((index) => Math.min(results.length - 1, Math.max(0, index + step)))
    } else if (event.key === 'Enter') {
      if (listOpen && activeIndex >= 0 && results[activeIndex]) {
        event.preventDefault()
        choose(results[activeIndex])
      }
    } else if (event.key === 'Escape') {
      // A search field normally empties itself on Escape; here Escape only
      // closes the list and keeps what was typed (the field's own clear
      // button still empties it).
      event.preventDefault()
      setListOpen(false)
    }
  }

  const showPanel = listOpen && status !== 'idle'

  return (
    <div className="search-box">
      <input
        type="search"
        className="search-input"
        placeholder="Search species (scientific or common name)"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setListOpen(true)
        }}
        onFocus={() => setListOpen(true)}
        onBlur={() => setListOpen(false)}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-label="Search species"
        aria-expanded={showPanel && status === 'results'}
        aria-controls={LIST_ID}
        aria-autocomplete="list"
        aria-activedescendant={
          showPanel && activeIndex >= 0 ? `${LIST_ID}-${activeIndex}` : undefined
        }
        autoComplete="off"
        spellCheck="false"
      />

      {showPanel && (
        <div className="search-panel">
          <SearchMessage status={status} query={query} errorMessage={errorMessage} />

          {status === 'results' && (
            <>
              <ul id={LIST_ID} className="search-results" role="listbox">
                {results.map((result, index) => (
                  <li
                    key={result.specieskey}
                    id={`${LIST_ID}-${index}`}
                    role="option"
                    aria-selected={index === activeIndex}
                    className={index === activeIndex ? 'search-result active' : 'search-result'}
                    // Keeps the focus in the text field, so the list does not
                    // close before the click lands.
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => choose(result)}
                  >
                    <SearchResult result={result} />
                  </li>
                ))}
              </ul>
              <SourceNote results={results} />
            </>
          )}
        </div>
      )}
    </div>
  )
}

// The one-line message above the list: still too short, searching, nothing
// found, or what went wrong. Shows nothing when there are results.
function SearchMessage({ status, query, errorMessage }) {
  const messages = {
    too_short: `Type at least ${MIN_SEARCH_CHARACTERS} letters or digits to search.`,
    loading: 'Searching...',
    empty: `No species found for "${query.trim()}". Only species recorded in BC can be found.`,
    error: errorMessage,
  }
  if (!messages[status]) {
    return null
  }
  return (
    <div className={status === 'error' ? 'search-message error' : 'search-message'}>
      {messages[status]}
    </div>
  )
}

// One species in the list. The scientific name comes first, because many
// common names are shared by several species; then the species' common
// name, if BCSEE gives one; then a BCSEE entry's name if that is what matched,
// clearly labelled so it is never mistaken for the species' common name; then
// the family and record count, which tell apart two cards with the same name.
function SearchResult({ result }) {
  const entryNames = result.display_names.filter((name) => ENTRY_NAME_LABELS[name.field])

  return (
    <>
      <div className="result-name">
        <span className="scientific-name">{result.col_name}</span>
        {result.col_authorship && (
          <span className="result-authorship"> {result.col_authorship}</span>
        )}
      </div>

      {result.english_name && (
        <div className="result-line">
          {result.english_name}
          <span className="result-tag">Common name from BCSEE</span>
        </div>
      )}

      {entryNames.map((name) => (
        <div className="result-line" key={`${name.field}:${name.text}`}>
          {name.text}
          <span className="result-tag">
            {ENTRY_NAME_LABELS[name.field]}
            {name.bcsee_entry_scientific_label &&
              name.bcsee_entry_scientific_label !== result.col_name &&
              ` (${name.bcsee_entry_scientific_label})`}
          </span>
        </div>
      ))}

      <div className="result-details">
        {[
          result.family,
          `${result.record_count.toLocaleString()} ${result.record_count === 1 ? 'record' : 'records'}`,
          result.merged_from
            ? `key ${result.merged_from}, merged into ${result.specieskey}`
            : result.match.kind === 'key' && `key ${result.specieskey}`,
        ]
          .filter(Boolean)
          .join(' · ')}
      </div>
    </>
  )
}

// The line under the list saying where the names come from, taken from the
// results themselves so it always matches what the API sent.
function SourceNote({ results }) {
  const nameSource = results[0]?.name_source
  const bcseeSource = results
    .flatMap((result) => result.display_names)
    .find((name) => name.field !== 'col_name')?.source

  return (
    <div className="search-sources">
      {nameSource && `Scientific names from ${nameSource}.`}
      {bcseeSource && ` Common names and BCSEE names from ${bcseeSource}.`}
    </div>
  )
}
