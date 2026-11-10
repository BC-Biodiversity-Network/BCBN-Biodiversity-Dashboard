import { useEffect, useState } from 'react'
import { cellArea } from 'h3-js'

import { cellExportUrl, fetchCell } from './api'
import CsvDownload from './CsvDownload'
import { GBIF_COUNT_NOTE, gbifCellSearchUrl, gbifLeftOutNote } from './gbifLinks'
import RankName, { rankTitle } from './RankName'
import { NO_LIST, mapSearch, rankKey, speciesMapSearch } from './route'
import SpeciesListControls, { SpeciesListPaging, noMatchText, shownText } from './SpeciesListControls'
import YearBars from './YearBars'
import { yearsLabel } from './yearRange'

// How many species one page of the all-species list holds.
export const PAGE_SIZE = 50

// The statuses that get a coloured chip, as on the species card.
const STATUS_CLASSES = { Red: 'status-red', Blue: 'status-blue', Yellow: 'status-yellow', Exotic: 'status-exotic' }

// A hexagon's size in words, from its id: "Hexagon of about 5.2 km²". The
// area is the hexagon's own (hexagons vary a little in size), to one decimal
// below 10 km² and to the nearest km² above. Visitors are told the size,
// never H3's resolution number.
export function hexagonSizeText(cell) {
  return `Hexagon of about ${km2Text(cellArea(cell, 'km2'))}`
}

// Writes an area for visitors: to one decimal below 10 km², else to the
// nearest km², with commas ("5.2 km²", "1,504 km²").
export function km2Text(value) {
  return value < 10 ? `${value.toFixed(1)} km²` : `${Math.round(value).toLocaleString('en-US')} km²`
}

// The hexagon's totals in words: "6 records of 2 species, all years" or
// "3 records of 1 species, 2018 to 2020".
export function totalsText(data) {
  const records = `${data.records.toLocaleString()} ${data.records === 1 ? 'record' : 'records'}`
  const species = `${data.species.toLocaleString()} species`
  const years = data.years_counted ? yearsLabel(data.years_counted) : 'all years'
  return `${records} of ${species}, ${years}`
}

// "1 record" or "12 records".
function recordsWord(count) {
  return `${count.toLocaleString()} ${count === 1 ? 'record' : 'records'}`
}

// The note when a year range left out records with no date (count of them),
// or null.
export function notCountedText(count) {
  return count ? `${recordsWord(count)} with no date ${count === 1 ? 'is' : 'are'} not counted.` : null
}

// A species' name for the list: the scientific name in italics, then the
// English name from BCSEE when it has one.
function SpeciesNameText({ item }) {
  return (
    <>
      <i>{item.col_name}</i>
      {item.english_name && <span className="hexagon-english"> {item.english_name}</span>}
    </>
  )
}

// A link that calls onFollow() in place of following it (a plain click
// only; with a modifier key it opens as a normal link). Other props (a
// title, an aria-label) go on the link.
export function InPageLink({ href, onFollow, children, ...rest }) {
  return (
    <a
      {...rest}
      href={href}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
          return
        }
        event.preventDefault()
        onFollow()
      }}
    >
      {children}
    </a>
  )
}

// Loads one hexagon's answer from /api/hexes/{cell} for the options given
// (see fetchCell), again whenever they change (see useListAnswer).
function useCellAnswer(cell, options) {
  return useListAnswer([cell, options], (signal) => fetchCell(cell, { ...options, signal }))
}

// Loads an answer with a list of species (a hexagon's, an area's) for what
// asking describes (anything that can be written as JSON: the same asking
// gives the same answer), with load(signal), again whenever asking changes;
// a newer request cancels an older one, so a slow answer can never replace
// a newer one. Returns { current, data }: current is the answer (or
// { error }) for this asking, or null while it loads; data is the last
// answer that arrived, kept on screen while a new one loads.
export function useListAnswer(asking, load) {
  const [loaded, setLoaded] = useState(null)
  const request = JSON.stringify(asking)

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
      .then((data) => setLoaded({ request, data }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          console.error(error)
          setLoaded({ request, error })
        }
      })
    return () => controller.abort()
    // request holds everything the fetch depends on, so it is the only
    // dependency.
  }, [request])

  const current = loaded?.request === request ? loaded : null
  return { current, data: current?.data ?? loaded?.data ?? null }
}

// The message when the answer could not be loaded.
function LoadError({ error }) {
  return (
    <p className="card-message error">
      {error.kind === 'unreachable'
        ? 'This hexagon is not available: the API could not be reached.'
        : `This hexagon could not be loaded: ${error.message}`}
    </p>
  )
}

// One species in the all-species list: its names (a link that opens its
// card and map), its BCSEE status chips and its records in the hexagon.
// onOpen(key) opens it in place of following the link.
export function SpeciesRow({ item, onOpen }) {
  return (
    <li className="hexagon-row">
      <div className="hexagon-row-line">
        <span className="hexagon-row-names">
          <InPageLink href={speciesMapSearch(item.specieskey)} onFollow={() => onOpen(item.specieskey)}>
            <SpeciesNameText item={item} />
          </InPageLink>
          {item.statuses.map((status) => (
            <span key={status} className={`status-chip hexagon-chip ${STATUS_CLASSES[status]}`}
              title={`BCSEE status: ${status}`}>
              {status}
            </span>
          ))}
        </span>
        <span className="hexagon-records" title="Records of this species in the hexagon">
          {item.records.toLocaleString()}
        </span>
      </div>
    </li>
  )
}

// The panel on the overview: the hexagon's records and species for the
// chosen years, and its species, a page of PAGE_SIZE at a time, with a
// filter by name and by BC status and a choice of order (see
// SpeciesListControls.jsx). list holds those options ({ q, sort, status,
// page }, from the address) and onListChange(list) changes them.
// onOpenSpecies(key) opens a species' card and map. On a rank map (rank is
// { rank, name, path }), only the rank's species, with a link to every
// species in the hexagon (onShowAllSpecies).
function AllSpeciesPanel({
  cell, years, rank = null, list, onListChange, onClose, onOpenSpecies, onShowAllSpecies,
}) {
  // The list's options: the same ones give the page shown and the CSV
  // download of the whole list.
  const options = {
    years, q: list.q, status: list.status, sort: list.sort, rank: rank ? { rank: rank.rank, path: rank.path } : null,
  }
  const { current, data } = useCellAnswer(cell, { ...options, offset: (list.page - 1) * PAGE_SIZE, limit: PAGE_SIZE })
  const shown = data ? shownText(data, list) : null
  const notCounted = data?.years_counted ? notCountedText(data.records_no_year_left_out) : null

  return (
    <aside className="species-card hexagon-panel" aria-label="Species in this hexagon" aria-busy={!current}>
      <button type="button" className="card-close" onClick={onClose} aria-label="Close the list of species in this hexagon">
        ×
      </button>
      <h2 className="hexagon-title">
        {rank ? <>{rankTitle(rank.rank)} <RankName rank={rank.rank} name={rank.name} /> in this hexagon</>
          : 'Species in this hexagon'}
      </h2>
      <p className="hexagon-size">{hexagonSizeText(cell)}</p>
      {current?.error && <LoadError error={current.error} />}
      {!data && !current?.error && <p className="card-message">Loading the species in this hexagon...</p>}
      {data && (
        <>
          <p className="hexagon-totals">{totalsText(data)}</p>
          {notCounted && <p className="hexagon-note">{notCounted}</p>}
          {data.species > 0 && (
            <>
              <SpeciesListControls list={list} data={data} onChange={onListChange}
                filterLabel="Filter the species in this hexagon by name" />
              {shown && <p className="species-list-shown">{shown}</p>}
              {/* The whole list as a CSV file, for the options asked for;
                  not while they load, after an error, or with nothing to list. */}
              <CsvDownload href={cellExportUrl(cell, options)}
                disabled={!current || Boolean(current.error) || data.matching === 0}
                label="Download the species in this hexagon as a CSV file" />
              {data.matching === 0
                ? <p className="hexagon-note">{noMatchText(list)}</p>
                : (
                  <ol className="hexagon-species" start={data.offset + 1}>
                    {data.results.map((item) => (
                      <SpeciesRow key={item.specieskey} item={item} onOpen={onOpenSpecies} />
                    ))}
                  </ol>
                )}
              <SpeciesListPaging data={data} list={list} onChange={onListChange} />
            </>
          )}
          {rank && data.species === 0 && (
            <p className="hexagon-note">
              No species of this {rank.rank} in this hexagon{data.years_counted ? ' for these years' : ''}.
            </p>
          )}
          {rank && (
            <p className="hexagon-show-all">
              <InPageLink href={mapSearch({ cell })} onFollow={onShowAllSpecies}>
                Show all species in this hexagon
              </InPageLink>
            </p>
          )}
          <HexagonGbifLink cell={cell} yearsCounted={data.years_counted} rank={rank} list={list} />
          <p className="hexagon-sources">
            Scientific names from the Catalogue of Life, via GBIF; English names and statuses
            from {data.english_name_source}. Records counted after merging keys, as on the maps.
          </p>
        </>
      )}
    </aside>
  )
}

// The all-species panel's link to every record in the hexagon on GBIF, in a
// new tab: the hexagon's own outline as GBIF's location, every species, and
// the years the answer counted (yearsCounted, [from, to], or null for all
// years). Under it, what GBIF's count may differ by, and, when the list has
// filters the link cannot carry (a rank, a name filter, a BC status filter;
// rank and list as the panel has them), a note naming them.
function HexagonGbifLink({ cell, yearsCounted, rank, list }) {
  const leftOut = gbifLeftOutNote({ place: 'hexagon', rank, list })
  return (
    <>
      <p className="hexagon-gbif">
        <a href={gbifCellSearchUrl({ cell, years: yearsCounted })} target="_blank" rel="noopener noreferrer">
          See these records on GBIF
        </a>
      </p>
      <p className="hexagon-note">{GBIF_COUNT_NOTE}</p>
      {leftOut && <p className="hexagon-note">{leftOut}</p>}
    </>
  )
}

// The records of the species in the hexagon, when it has some: how many
// and when, the chart by year, the note about records with no date, and the
// link to the same records on GBIF.
function SpeciesRecords({ asked, cell, yearsCounted }) {
  const range = yearsCounted ? yearsLabel(yearsCounted) : 'all years'
  const noDate = asked.records_no_year
  const noDateNote = yearsCounted
    ? notCountedText(noDate)
    : noDate > 0 && `${noDate.toLocaleString()} of these records ${noDate === 1 ? 'has' : 'have'} no date, so `
      + `${noDate === 1 ? 'it is' : 'they are'} not in the chart.`
  const datedRecords = asked.records_per_year.reduce((sum, item) => sum + item.records, 0)

  return (
    <>
      <p className="hexagon-totals">
        <strong>{asked.records.toLocaleString()}</strong> {asked.records === 1 ? 'record' : 'records'} in this
        hexagon, {range}.
      </p>
      {datedRecords > 0 && (
        <p className="hexagon-years">
          {asked.first_year === asked.last_year
            ? `All from ${asked.first_year}.`
            : `First record ${asked.first_year}, last ${asked.last_year}.`}
        </p>
      )}
      {datedRecords > 0 && <YearBars perYear={asked.records_per_year} span={yearsCounted} />}
      {noDateNote && <p className="hexagon-note">{noDateNote}</p>}
      <p className="hexagon-gbif">
        <a href={gbifCellSearchUrl({ keys: asked.gbif_keys, cell, years: yearsCounted })} target="_blank"
          rel="noopener noreferrer">
          See these {recordsWord(asked.records)} on GBIF
        </a>
      </p>
      <p className="hexagon-note">{GBIF_COUNT_NOTE}</p>
    </>
  )
}

// The panel on a species map: that species alone in the hexagon, its
// records for the chosen years (or that it has none, or, when the
// sensitive-species rule hides it at this size, nothing about them), and a
// link to show every species in the hexagon on the overview
// (onShowAllSpecies).
function SpeciesPanel({ cell, years, species, onClose, onShowAllSpecies }) {
  const { current, data } = useCellAnswer(cell, { years, species: species.key, limit: 1 })
  const asked = data?.species_asked_for ?? null
  const name = asked?.col_name ?? species.name

  return (
    <aside className="species-card hexagon-panel" aria-label="This species in this hexagon" aria-busy={!current}>
      <button type="button" className="card-close" onClick={onClose} aria-label="Close this hexagon">
        ×
      </button>
      <h2 className="hexagon-title">{name ? <i>{name}</i> : 'This species'} in this hexagon</h2>
      <p className="hexagon-size">{hexagonSizeText(cell)}</p>
      {current?.error && <LoadError error={current.error} />}
      {!data && !current?.error && <p className="card-message">Loading this hexagon...</p>}
      {asked && asked.records === null && (
        <p className="hexagon-totals">Records of this species are not shown for this hexagon.</p>
      )}
      {asked && asked.records === 0 && (
        <>
          <p className="hexagon-totals">
            No records of <i>{asked.col_name}</i> in this hexagon
            {data.years_counted ? `, ${yearsLabel(data.years_counted)}` : ''}.
          </p>
          {data.years_counted && notCountedText(asked.records_no_year) && (
            <p className="hexagon-note">{notCountedText(asked.records_no_year)}</p>
          )}
        </>
      )}
      {asked && asked.records > 0 && <SpeciesRecords asked={asked} cell={cell} yearsCounted={data.years_counted} />}
      {data && data.species > 0 && (
        <p className="hexagon-show-all">
          <InPageLink href={speciesMapSearch(null, cell)} onFollow={onShowAllSpecies}>
            Show all {data.species.toLocaleString()} species in this hexagon
          </InPageLink>
        </p>
      )}
      {asked && (
        <p className="hexagon-sources">
          Name from the Catalogue of Life, via GBIF. Records counted after merging keys, as on the maps.
        </p>
      )}
    </aside>
  )
}

// The panel for one clicked hexagon, in the side panel where the species
// card goes. On the overview (species and rank null) it lists every species
// in the hexagon (see AllSpeciesPanel); on a species map (species is { key,
// name, ... }) it is about that species alone (see SpeciesPanel); on a rank
// map (rank is { rank, name, path }) it lists that rank's species in the
// hexagon, with a link to every species there. onOpenSpecies(key) opens a
// species' card and map, onShowAllSpecies switches to the overview's panel
// for the same hexagon, and onClose closes the panel. list and
// onListChange are the options of its species list (see AllSpeciesPanel).
// The page gives the panel the hexagon id as its React key, so each hexagon
// starts afresh.
export default function HexagonPanel({
  cell, years, species, rank = null, list = NO_LIST, onListChange = () => {}, onClose, onOpenSpecies,
  onShowAllSpecies,
}) {
  if (species) {
    return <SpeciesPanel cell={cell} years={years} species={species} onClose={onClose} onShowAllSpecies={onShowAllSpecies} />
  }
  return (
    <AllSpeciesPanel key={rank ? rankKey(rank) : 'all'} cell={cell} years={years} rank={rank} list={list}
      onListChange={onListChange} onClose={onClose} onOpenSpecies={onOpenSpecies} onShowAllSpecies={onShowAllSpecies} />
  )
}
