import { useEffect } from 'react'

import { areaExportUrl, fetchArea } from './api'
import { averageHexagonKm2, boxAreaKm2 } from './area'
import CsvDownload from './CsvDownload'
import { AREA_GBIF_NOTE, GBIF_COUNT_NOTE, gbifBoxSearchUrl } from './gbifLinks'
import { PAGE_SIZE, SpeciesRow, km2Text, notCountedText, totalsText, useListAnswer } from './HexagonPanel'
import RankName, { rankTitle } from './RankName'
import SpeciesListControls, { SpeciesListPaging, noMatchText, shownText } from './SpeciesListControls'

// The line under the title: the box's size, how many hexagons it touches
// and how big they are, for example "A box of about 979 km² touching 45
// hexagons of about 36 km² each." (the size, never H3's resolution number).
export function areaSizeText(area, hexagonCount) {
  return `A box of about ${km2Text(boxAreaKm2(area.box))} touching ${hexagonCount.toLocaleString('en-US')} `
    + `${hexagonCount === 1 ? 'hexagon' : 'hexagons'} of about ${km2Text(averageHexagonKm2(area.res))} each.`
}

// The line saying what is counted.
export const WHOLE_HEXAGONS_NOTE = 'It counts the whole of every hexagon the box touches, so the area counted '
  + 'is larger than the box.'

// The message when the area's answer could not be loaded: the API's own
// words for a request it refused (a box too small or too big, say), else
// that the API could not be reached or failed.
function AreaError({ error }) {
  let message = `This area could not be loaded: ${error.message}`
  if (error.kind === 'rejected') {
    message = error.message
  } else if (error.kind === 'unreachable') {
    message = 'This area is not available: the API could not be reached.'
  }
  return <p className="card-message error">{message}</p>
}

// The panel for a box drawn on the map, in the side panel where the species
// card goes (like the hexagon panel): the box's size and its hexagons, the
// records and species in them for the chosen years, and their species, a
// page of PAGE_SIZE at a time, with the hexagon panel's filter by name, BC
// status, order and page buttons, a Download CSV button, and a link to the
// box's records on GBIF with what to expect there. area is { box, res } (see
// readArea in route.js) and cellCount how many hexagons the map outlined for
// it (boxCells in area.js): a different count from the API is reported in
// the browser's console. On a rank map (rank is { rank, name, path }), only
// the rank's species. list holds the list's options ({ q, sort, status,
// page }, from the address) and onListChange(list) changes them;
// onOpenSpecies(key) opens a species' card and map; onClose is the x.
export default function AreaPanel({
  area, cellCount, years, rank = null, list, onListChange, onClose, onOpenSpecies,
}) {
  // The list's options: the same ones give the page shown and the CSV
  // download of the whole list.
  const options = {
    years, q: list.q, status: list.status, sort: list.sort, rank: rank ? { rank: rank.rank, path: rank.path } : null,
  }
  const { current, data } = useListAnswer(
    ['area', area, options, list.page],
    (signal) => fetchArea(area, { ...options, offset: (list.page - 1) * PAGE_SIZE, limit: PAGE_SIZE, signal }),
  )
  const shown = data ? shownText(data, list) : null
  const notCounted = data?.years_counted ? notCountedText(data.records_no_year_left_out) : null

  // The API and the map must count the same hexagons; if they ever do not,
  // say so plainly in the browser's console, for whoever looks into it.
  useEffect(() => {
    if (data && data.hexagon_count !== cellCount) {
      console.warn(`Area search: the API counted ${data.hexagon_count} hexagons under the box `
        + `${JSON.stringify(area.box)} at resolution ${area.res}, but the map outlines ${cellCount}. `
        + 'The two should find exactly the same hexagons (see area.js and backend/api/area.py).')
    }
  }, [data, cellCount])

  return (
    <aside className="species-card hexagon-panel area-panel" aria-label="Species in this area" aria-busy={!current}>
      <button type="button" className="card-close" onClick={onClose} aria-label="Close the list of species in this area">
        ×
      </button>
      <h2 className="hexagon-title">
        {rank ? <>{rankTitle(rank.rank)} <RankName rank={rank.rank} name={rank.name} /> in this area</>
          : 'Species in this area'}
      </h2>
      <p className="hexagon-size">{areaSizeText(area, cellCount)}</p>
      <p className="hexagon-note">{WHOLE_HEXAGONS_NOTE}</p>
      {current?.error && <AreaError error={current.error} />}
      {!data && !current?.error && <p className="card-message">Loading the species in this area...</p>}
      {data && !current?.error && (
        <>
          <p className="hexagon-totals">{totalsText(data)}</p>
          {notCounted && <p className="hexagon-note">{notCounted}</p>}
          {data.species > 0 && (
            <>
              <SpeciesListControls list={list} data={data} onChange={onListChange}
                filterLabel="Filter the species in this area by name" />
              {shown && <p className="species-list-shown">{shown}</p>}
              {/* The whole list as a CSV file, for the options asked for;
                  not while they load or with nothing to list. */}
              <CsvDownload href={areaExportUrl(area, options)} disabled={!current || data.matching === 0}
                label="Download the species in this area as a CSV file" />
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
          {data.species === 0 && (
            <p className="hexagon-note">
              No {rank ? `species of this ${rank.rank}` : 'species'} in this area
              {data.years_counted ? ' for these years' : ''}.
            </p>
          )}
          <p className="hexagon-gbif">
            <a href={gbifBoxSearchUrl({ box: area.box, years: data.years_counted })} target="_blank"
              rel="noopener noreferrer">
              See these records on GBIF
            </a>
          </p>
          <p className="hexagon-note">{AREA_GBIF_NOTE}</p>
          <p className="hexagon-note">{GBIF_COUNT_NOTE}</p>
          {rank && (
            <p className="hexagon-note">
              GBIF shows every species in the box, not only this {rank.rank}.
            </p>
          )}
          <p className="hexagon-sources">
            Scientific names from the Catalogue of Life, via GBIF; English names and statuses
            from {data.english_name_source}. Records counted after merging keys, as on the maps.
          </p>
        </>
      )}
    </aside>
  )
}
