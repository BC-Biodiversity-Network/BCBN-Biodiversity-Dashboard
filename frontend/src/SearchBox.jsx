import { Fragment, useEffect, useState } from 'react'

import { MIN_SEARCH_CHARACTERS, searchSpecies, searchableLength } from './api'
import RankName, { rankTitle } from './RankName'
import { rankKey } from './route'

// How long to wait after the last key press before searching, in
// milliseconds. Long enough that typing "robin" sends one request, not five;
// short enough that the list still feels immediate.
const SEARCH_DELAY_MS = 250

// How many species, and how many ranks, to ask the API for.
const RESULT_LIMIT = 20
const RANK_LIMIT = 5

// The heading of each group in the list. The ranks come after the species,
// under their own heading, so a rank found by a common word ("robin" finds
// the genus Robinia) is not taken for a species; they come first only when
// a rank's name is the query itself (see ranksFirst).
const SPECIES_HEADING = 'Species'
const RANKS_HEADING = 'Genera, families and higher ranks'

// The id of the results list, so the input can point screen readers at it.
const LIST_ID = 'species-search-results'

// The fields of display_names that are not the species' own names (a name
// from a BCSEE entry, or the name of a key merged into this one), and how
// each is labelled in the list. The merged name's label is a placeholder
// until the wording is decided.
const ENTRY_NAME_LABELS = {
  bcsee_entry_english_name: 'Common name of a BCSEE entry',
  bcsee_scientific_label: 'Scientific name from BCSEE',
  merged_name: 'Earlier name (Catalogue of Life synonym)',
}

// The search box: a text field with a list of matching species under it,
// then the matching ranks (genus to kingdom) as their own group. Calls
// onSelect(result) when a species is picked, with the result as the API
// sent it (its specieskey is always the kept key; merged_from says which old
// key was typed, if any), and onSelectRank({ rank, name, path }) when a rank
// is picked.
export default function SearchBox({ onSelect, onSelectRank = () => {} }) {
  const [query, setQuery] = useState('')
  // What the box is doing: 'idle' (empty), 'too_short', 'loading', 'results',
  // 'empty' (nothing found) or 'error'.
  const [status, setStatus] = useState('idle')
  // The species found, then the ranks, as one list for the keyboard:
  // [{ kind: 'species' or 'rank', result }].
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
      searchSpecies(query, { limit: RESULT_LIMIT, rankLimit: RANK_LIMIT, signal: controller.signal })
        .then((answer) => {
          // An answer to a search that has since been replaced is ignored.
          // fetch already stops a cancelled request, so this is a second
          // guard in case an answer still arrives.
          if (controller.signal.aborted) {
            return
          }
          const species = answer.results.map((result) => ({ kind: 'species', result }))
          const ranks = (answer.ranks ?? []).map((result) => ({ kind: 'rank', result }))
          const found = ranksFirst(answer.ranks ?? [], query) ? [...ranks, ...species] : [...species, ...ranks]
          setResults(found)
          setActiveIndex(found.length > 0 ? 0 : -1)
          setStatus(found.length > 0 ? 'results' : 'empty')
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
  function choose({ kind, result }) {
    if (kind === 'rank') {
      onSelectRank({ rank: result.rank, name: result.name, path: result.path })
    } else {
      onSelect(result)
    }
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
                {results.map((item, index) => (
                  <Fragment key={item.kind === 'rank' ? `rank:${rankKey(item.result)}` : item.result.specieskey}>
                    <GroupHeading results={results} index={index} />
                    <li
                      id={`${LIST_ID}-${index}`}
                      role="option"
                      aria-selected={index === activeIndex}
                      className={index === activeIndex ? 'search-result active' : 'search-result'}
                      // Keeps the focus in the text field, so the list does
                      // not close before the click lands.
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => choose(item)}
                    >
                      {item.kind === 'rank' ? <RankResult result={item.result} /> : <SearchResult result={item.result} />}
                    </li>
                  </Fragment>
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

// Whether the ranks go above the species: when a rank's name is exactly
// the query, ignoring case and the spaces around it ("Turdus", "aves"), so
// the rank someone typed comes first and Enter opens it. Otherwise ("robin",
// which finds the genus Robinia) the species come first.
export function ranksFirst(ranks, query) {
  const typed = query.trim().toLowerCase()
  return ranks.some((rank) => rank.name.toLowerCase() === typed)
}

// The heading before the result at index, when it starts a group: none
// when only species were found (as before ranks were searched); "Species"
// and the ranks' heading when both groups are there; only the ranks'
// heading when no species was found. Screen readers skip it (the options
// say what they are), so it does not count as an option.
function GroupHeading({ results, index }) {
  const kind = results[index].kind
  if (index > 0 && results[index - 1].kind === kind) {
    return null
  }
  const anyRank = results.some((item) => item.kind === 'rank')
  if (kind === 'species' && !anyRank) {
    return null
  }
  return (
    <li role="presentation" className="search-group">
      {kind === 'rank' ? RANKS_HEADING : SPECIES_HEADING}
    </li>
  )
}

// One rank in the list: its label from the API ("Genus Robinia (Fabaceae)",
// the parent telling apart ranks of the same name), the genus in italics,
// then its species and records in BC.
function RankResult({ result }) {
  const start = `${rankTitle(result.rank)} ${result.name}`
  const rest = result.label.startsWith(start) ? result.label.slice(start.length) : null
  return (
    <>
      <div className="result-name">
        {rest === null
          ? result.label
          : <>{rankTitle(result.rank)} <RankName rank={result.rank} name={result.name} />{rest}</>}
      </div>
      <div className="result-details">
        {`${result.species_count.toLocaleString()} species · ${result.record_count.toLocaleString()} `
          + `${result.record_count === 1 ? 'record' : 'records'}`}
      </div>
    </>
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
  const species = results.filter((item) => item.kind === 'species').map((item) => item.result)
  const nameSource = species[0]?.name_source
  const bcseeSource = species
    .flatMap((result) => result.display_names)
    .find((name) => name.field !== 'col_name' && name.field !== 'merged_name')?.source
  const anyRank = results.some((item) => item.kind === 'rank')

  return (
    <div className="search-sources">
      {nameSource && `Scientific names from ${nameSource}.`}
      {bcseeSource && ` Common names and BCSEE names from ${bcseeSource}.`}
      {anyRank && ' Ranks from the GBIF classification.'}
    </div>
  )
}
