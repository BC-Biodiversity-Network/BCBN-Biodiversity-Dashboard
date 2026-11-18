// How the map shows an area of interest, kept apart from HexMap.jsx so the
// map itself changes little.
//
// An area has its own hexagon size (Auto or picked, see areaSize.js), which
// no longer follows the map's zoom. When the map's hexagons are larger than
// the area's, a large map hexagon is usually only partly in the area, so the
// map cannot show the area with its own hexagons: every map hexagon is
// faded, and the area's own hexagons are drawn on top of them, at the area's
// size, coloured on the same scale as the map (useAreaHexagons and
// areaHexagonLayer). When the map's hexagons are the same size as the area's
// or smaller, each map hexagon is either in the area or not, and the map
// keeps them bright or faded as before (selectionTest in area.js).
//
// Order on the map, bottom to top: the map's hexagons, the area's own
// hexagons (both deck.gl layers just under the basemap's labels), then the
// shapes' outline (a MapLibre line layer added last, in HexMap.jsx).

import { useEffect, useMemo, useRef, useState } from 'react'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { getResolution } from 'h3-js'

import { selectionTest } from './area'
import { areaText } from './areaShapes'
import { sizeKm } from './areaSize'
import { COLOUR_SCHEMES, HEXAGON_OUTLINE, hexagonColour, outlineOpacity } from './colours'
import { useOverviewHexes, useRankHexes } from './speciesHexes'
import { rankKey } from './route'
import { yearsKey } from './yearRange'
import { hexagonSizeLabel, hexagonWidthPixels } from './zoomTiers'

// The outline of each of the area's shapes (the boxes and polygons the
// visitor made): a dark solid line on a wider white one, so it reads on
// light and dark hexagons of either scale (blue or orange), and on the
// basemap. It is the only outline the area has, at every zoom.
export const SHAPE_OUTLINE = { dark: '#1a1a1a', light: '#ffffff', darkWidth: 2, lightWidth: 4.5 }

// The fill of the area's own hexagons that have no records, drawn with the
// others above the faded map: a light plain grey, unlike any colour of
// either scale, so they read as counted but empty rather than as faded land
// outside the area.
export const EMPTY_AREA_FILL = [238, 238, 238, 217]

// The hover box over one of those: there is nothing in it to open.
export const EMPTY_AREA_TOOLTIP = { html: '<div>No records</div>' }

/**
 * A short text naming an area (its hexagon size and its area text), to tell
 * areas apart: in layer ids, in deck.gl's update triggers and in React keys.
 */
export function areaId(area) {
  return `${area.res}:${areaText(area.shapes)}`
}

/**
 * The box around an area's shapes, as [[west, south], [east, north]], from
 * every corner of every shape, for the map to fit the area in view.
 */
export function shapesBounds(shapes) {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const shape of shapes) {
    for (const [lng, lat] of shape.ring) {
      west = Math.min(west, lng)
      east = Math.max(east, lng)
      south = Math.min(south, lat)
      north = Math.max(north, lat)
    }
  }
  return [[west, south], [east, north]]
}

/**
 * Whether the map's hexagons (at mapRes) are larger than the area's (area,
 * or null), so the area's own hexagons are drawn on top of the map.
 */
export function mapIsCoarser(area, mapRes) {
  return Boolean(area) && mapRes < area.res
}

/**
 * The rows of a map (each { h3Cell, ... }) whose hexagon is one of the
 * area's (cellSet, a Set of H3 ids at the rows' resolution): the area's own
 * hexagons that have records.
 */
export function rowsInArea(rows, cellSet) {
  return rows.filter((row) => cellSet.has(row.h3Cell))
}

/**
 * One cache of map answers from the API for the life of the map, shared by
 * the map's own hooks and useAreaHexagons (see useHexesFromApi in
 * speciesHexes.js).
 */
export function useAnswerCache() {
  return useRef(new Map()).current
}

/**
 * The area's hexagons (cells) that are not among rows (the ones with
 * records), each as { h3Cell, empty: true }.
 */
export function emptyCells(cells, rows) {
  const withRecords = new Set(rows.map((row) => row.h3Cell))
  return cells.filter((cell) => !withRecords.has(cell)).map((cell) => ({ h3Cell: cell, empty: true }))
}

/**
 * The map data at the area's own size, and what the map does with it.
 *
 * area is the area shown ({ shapes, res, cells }, or null), mapRes the
 * resolution the map shows now. The data is the same map as on screen, at
 * the area's size: the shipped overview file (overviewRows[area.res]) for
 * all years with no rank, else the API's map for the year range (years) or
 * the rank (rankRef, { rank, path }), asked for only while the map's
 * hexagons are larger than the area's, and kept for the rest of the visit
 * (see useHexesFromApi), so it is fetched once. cache is the answer cache
 * the map's own hooks use, so neither asks for a map the other has.
 *
 * Returns:
 *   drawn       whether the area's own hexagons are drawn now (the map is
 *               coarser and the data is here)
 *   rows        the area's hexagons with records, at the area's size, and
 *               after them those with none (see emptyCells)
 *   layerKey    names the data (overview, years, rank) for the layer's id
 *   selection   for the map's own layers: { test, key }, see mapSelection
 *   shows(cell) whether the area's layer draws this hexagon now
 */
export function useAreaHexagons({ area, mapRes, overviewRows, years = null, rankRef = null, cache = null }) {
  const coarser = mapIsCoarser(area, mapRes)
  const res = area?.res ?? 7
  // Only one of the two is asked for, and only while it is needed.
  const rankHexes = useRankHexes(coarser && rankRef ? rankRef : null, years, res, cache)
  const yearHexes = useOverviewHexes(coarser && !rankRef ? years : null, res, cache)

  let sourceRows = null
  let layerKey = 'overview'
  if (area && rankRef) {
    const { data, loading } = rankHexes
    sourceRows = !loading && data?.resolution === res ? data.rows : null
    layerKey = `rank-${rankKey(rankRef)}-${yearsKey(years) || 'all'}`
  } else if (area && years) {
    const { data, loading } = yearHexes
    sourceRows = !loading && data?.resolution === res ? data.rows : null
    layerKey = `years-${yearsKey(years)}`
  } else if (area) {
    sourceRows = overviewRows?.[res] ?? null
  }

  // The area's hexagons as a Set, and the map's rule for which of its
  // hexagons are in the area, each worked out once per area.
  const cellSet = useMemo(() => (area ? new Set(area.cells) : null), [area])
  const baseTest = useMemo(() => (area ? selectionTest(area.cells, area.res) : null), [area])
  const rows = useMemo(() => {
    if (!sourceRows || !cellSet) {
      return null
    }
    const inside = rowsInArea(sourceRows, cellSet)
    return [...inside, ...emptyCells(area.cells, inside)]
  }, [sourceRows, cellSet])
  const ready = rows !== null
  const selection = useMemo(() => (area ? mapSelection(area, baseTest, ready) : null), [area, baseTest, ready])
  const drawn = coarser && ready

  return {
    drawn,
    rows: drawn ? rows : null,
    layerKey,
    selection,
    shows: (cell) => drawn && cellSet.has(cell),
  }
}

/**
 * Which of the map's own hexagons stay bright while an area is shown:
 * { test, key }, test(cell) true for a hexagon in the area, every other
 * one faded; key names the area and the rule, for deck.gl's update triggers.
 *
 * A map hexagon the same size as the area's or smaller is bright when it
 * lies in one of the area's hexagons (baseTest, selectionTest in area.js).
 * A larger one is always faded once the area's own hexagons are here
 * (ownReady), as they are drawn on top of it; until then (a year range or a
 * rank still loading at the area's size) it is bright only when all of it
 * is in the area, as before.
 */
export function mapSelection(area, baseTest, ownReady) {
  const key = areaId(area)
  if (!ownReady) {
    return { test: baseTest, key: `${key}|waiting` }
  }
  return { test: (cell) => getResolution(cell) >= area.res && baseTest(cell), key }
}

/**
 * The deck.gl layer of the area's own hexagons (own, from useAreaHexagons),
 * or null when they are not drawn: coloured like the map's hexagons on the
 * same scale (schemeName), never faded, those with no records in
 * EMPTY_AREA_FILL (HexMap gives them EMPTY_AREA_TOOLTIP and opens nothing
 * when one is clicked), with the white hexagon outline at
 * the strength for their size (outline, see useAreaOutline), and pickable,
 * so the hover box and a click are about these hexagons. beforeId puts it
 * just under the basemap's labels, above the map's own hexagons (it comes
 * after them in the list of layers).
 */
export function areaHexagonLayer(own, area, schemeName, outline, beforeId) {
  if (!own?.drawn || !area) {
    return null
  }
  const scheme = COLOUR_SCHEMES[schemeName]
  return new H3HexagonLayer({
    // Its own id for each area, data and size, so deck.gl never carries one
    // size's hexagon shapes over to another (see overviewLayerId in HexMap).
    id: `area-hexagons-${own.layerKey}-${areaId(area)}`,
    data: own.rows,
    getHexagon: (hexagon) => hexagon.h3Cell,
    getFillColor: (hexagon) => (hexagon.empty ? EMPTY_AREA_FILL : hexagonColour(hexagon, scheme)),
    // As for the map's layers: deck.gl works the colours out again only
    // when told to, here when the scale changes.
    updateTriggers: { getFillColor: [schemeName] },
    opacity: 1,
    filled: true,
    stroked: outline > 0,
    getLineColor: [...HEXAGON_OUTLINE.rgb, Math.round(outline * 255)],
    getLineWidth: HEXAGON_OUTLINE.widthPixels,
    lineWidthUnits: 'pixels',
    extruded: false,
    pickable: true,
    beforeId,
  })
}

/**
 * How strong the white outline of the area's own hexagons is (0 to 1, see
 * outlineOpacity), for their own size at the map's zoom. res is the area's
 * size (null for no area) and startZoom the map's zoom before it first
 * moves. Returns [outline, onZoom]: the map calls onZoom(zoom) whenever it
 * moves, and the outline only changes in quarter steps, so moving the map
 * does not redraw the page.
 */
export function useAreaOutline(res, startZoom) {
  const zoom = useRef(startZoom)
  const [outline, setOutline] = useState(() => (res ? outlineOpacity(hexagonWidthPixels(res, startZoom)) : 0))

  // A new size: the strength for it at the zoom the map is at.
  useEffect(() => {
    setOutline(res ? outlineOpacity(hexagonWidthPixels(res, zoom.current)) : 0)
  }, [res])

  /** Notes the map's new zoom, and changes the outline if its step changed. */
  function onZoom(next) {
    zoom.current = next
    const wanted = res ? outlineOpacity(hexagonWidthPixels(res, next)) : 0
    setOutline((current) => (current === wanted ? current : wanted))
  }
  return [outline, onZoom]
}

/**
 * The lines of the hexagon size label at the top left: one line for the
 * map's hexagons ("Hexagons ~52 km"), or, when an area's hexagons are of
 * another size than the map's, two: "Map: hexagons ~52 km" and "Area:
 * hexagons ~3 km".
 */
export function hexagonSizeLines(mapRes, area) {
  if (!area || area.res === mapRes) {
    return [hexagonSizeLabel(mapRes)]
  }
  return [`Map: hexagons ~${sizeKm(mapRes)} km`, `Area: hexagons ~${sizeKm(area.res)} km`]
}

/**
 * The legend's line about the area while its own hexagons are drawn on top
 * of larger map hexagons (own.drawn), or null: smaller hexagons hold fewer
 * species and records, so on the same scale they look paler.
 */
export function areaLegendNote(own, area) {
  if (!own?.drawn || !area) {
    return null
  }
  return `Inside the area: hexagons of about ${sizeKm(area.res)} km, on the same scale, so they look paler.`
}
