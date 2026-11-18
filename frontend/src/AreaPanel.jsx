import { useEffect } from 'react'

import { areaExportUrl, fetchArea } from './api'
import { averageHexagonKm2 } from './area'
import { areaText, shapeAreaKm2 } from './areaShapes'
import CsvDownload from './CsvDownload'
import { AREA_BC_ONLY_NOTE, GBIF_COUNT_NOTE, areaGbifNote, gbifAreaSearchUrl } from './gbifLinks'
import { PAGE_SIZE, SpeciesRow, km2Text, notCountedText, totalsText, useListAnswer } from './HexagonPanel'
import RankName, { rankTitle } from './RankName'
import SpeciesListControls, { SpeciesListPaging, noMatchText, shownText } from './SpeciesListControls'

// The line under the title: what the area is, how many hexagons it touches
// and how big they are (the size, never H3's resolution number). One shape
// keeps the wording a single box always had, with its size: "A box of about
// 979 km² touching 45 hexagons of about 36 km² each." (or "A polygon of
// about ..."); several shapes: "3 shapes touching 120 hexagons of about 36
// km² each." (their sizes can overlap, so no total is given).
export function areaSizeText(area, hexagonCount) {
  const hexagons = `${hexagonCount.toLocaleString('en-US')} ${hexagonCount === 1 ? 'hexagon' : 'hexagons'} of about `
    + `${km2Text(averageHexagonKm2(area.res))} each.`
  if (area.shapes.length === 1) {
    const [shape] = area.shapes
    return `A ${shape.isBox ? 'box' : 'polygon'} of about ${km2Text(shapeAreaKm2(shape))} touching ${hexagons}`
  }
  return `${area.shapes.length.toLocaleString('en-US')} shapes touching ${hexagons}`
}

// The line saying what is counted: the whole of every hexagon the area's
// shapes touch.
export function wholeHexagonsNote(area) {
  if (area.shapes.length === 1) {
    const what = area.shapes[0].isBox ? 'box' : 'polygon'
    return `It counts the whole of every hexagon the ${what} touches, so the area counted is larger than the ${what}.`
  }
  return 'It counts the whole of every hexagon the shapes touch, so the area counted is larger than the shapes.'
}

// The note beside "Download CSV" when the API says the area is too detailed
// to repeat its GBIF link on every row of the CSV (csv_link_once).
export const CSV_LINK_ONCE_NOTE = 'This area is detailed, so the CSV gives the GBIF link once at the top instead of on '
  + 'every row.'

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

// The panel for an area drawn on the map (one or more shapes), in the side
// panel where the species card goes (like the hexagon panel): the area's
// shapes and its hexagons, the records and species in them for the chosen
// years, and their species, a page of PAGE_SIZE at a time, with the hexagon
// panel's filter by name, BC status, order and page buttons, a Download CSV
// button, and a link to the area's records on GBIF (one geometry per shape)
// with what to expect there. area is { shapes, res } (see readArea in
// route.js) and cellCount how many hexagons the map outlined for it
// (shapesCells in areaShapes.js): a different count from the API is
// reported in the browser's console. On a rank map (rank is { rank, name, path }), only
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
      console.warn(`Area search: the API counted ${data.hexagon_count} hexagons under the area `
        + `${areaText(area.shapes)} at resolution ${area.res}, but the map outlines ${cellCount}. `
        + 'The two should find exactly the same hexagons (see areaShapes.js and backend/api/area.py).')
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
      <p className="hexagon-note">{wholeHexagonsNote(area)}</p>
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
              {data.csv_link_once && <p className="hexagon-note">{CSV_LINK_ONCE_NOTE}</p>}
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
            <a href={gbifAreaSearchUrl({ rings: area.shapes.map((shape) => shape.ring), years: data.years_counted })}
              target="_blank" rel="noopener noreferrer">
              See these records on GBIF
            </a>
          </p>
          <p className="hexagon-note">{AREA_BC_ONLY_NOTE}</p>
          <p className="hexagon-note">{areaGbifNote(area.shapes.length, area.shapes[0].isBox)}</p>
          <p className="hexagon-note">{GBIF_COUNT_NOTE}</p>
          {rank && (
            <p className="hexagon-note">
              GBIF shows every species in the {area.shapes.length === 1 && area.shapes[0].isBox ? 'box' : 'area'}, not
              only this {rank.rank}.
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
