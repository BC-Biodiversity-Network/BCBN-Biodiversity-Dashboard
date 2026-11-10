import { useEffect, useMemo, useRef, useState } from 'react'
import Map, { Layer, Source, useControl } from 'react-map-gl/maplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { cellToBoundary, cellToLatLng, getResolution } from 'h3-js'

// MapLibre ships its own stylesheet. Without it the map still draws, but the
// zoom buttons and the attribution line in the corner come out unstyled.
import 'maplibre-gl/dist/maplibre-gl.css'

import { boxFromCorners, selectionTest } from './area'
import { COLOUR_SCHEMES, HEXAGON_OUTLINE, OVERVIEW_SCHEMES, hexagonColour, outlineOpacity } from './colours'
import { getSpeciesTooltip, getTooltip } from './hexTooltip'
import Legend from './Legend'
import RankName from './RankName'
import { useOverviewHexes, useRankHexes, useSpeciesHexes } from './speciesHexes'
import YearFilter from './YearFilter'
import { yearsLabel } from './yearRange'
import {
  FIT_MAX_ZOOM, MAX_ZOOM, RESOLUTIONS, hexagonSizeLabel, hexagonWidthPixels, pickResolution, zoomShowingResolution,
} from './zoomTiers'

// The basemap style. Swap this one line to change providers (the Datasets
// page map uses it too).
export const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/bright'

// Where the map sits when the page opens: the middle of British Columbia,
// zoomed out far enough to see the whole province.
export const INITIAL_VIEW_STATE = {
  longitude: -125,
  latitude: 54.5,
  zoom: 4.5,
}

// Which overview scale the map opens on.
//
// Species, because it reads as a pattern where records read as noise. Measured
// at resolution 5: records run 11,231 times from the middle cell to the
// largest, while species run only 125 times, so the species surface is about
// ninety times flatter.
const DEFAULT_SCHEME = 'species'

// The basemap's place names start at this layer. Drawing the hexagons
// immediately before it puts them underneath every label, so city names stay
// readable through the semi-transparent fill.
const FIRST_LABEL_LAYER_ID = 'waterway_line_label'

// The width of the species card panel on the right (see .top-right-controls
// in index.css) plus its margins, so a species is never fitted under it.
const CARD_PANEL_PX = 380 + 12 + 24

// Each overview resolution gets its own layer id, and so does each species
// map at each resolution.
//
// They must not share an id. deck.gl matches layers between renders by id and
// carries internal state across the match, so when one id was used for all
// three resolutions the hexagon geometry worked out for one tier was handed to
// the next one and part of the map drew at the wrong cell size.
export function overviewLayerId(resolution) {
  return `bc-hexagons-r${resolution}`
}
export function speciesLayerId(speciesKey, resolution) {
  return `species-${speciesKey}-r${resolution}`
}
// The overview for a year range, from the API, gets its own id for each
// range and resolution, for the same reason.
export function filteredOverviewLayerId(yearsCounted, resolution) {
  return `overview-${yearsCounted.join('-')}-r${resolution}`
}
// A rank's map gets its own id for each rank, year range and resolution,
// for the same reason. rankId is rankKey(rank) (route.js).
export function rankLayerId(rankId, yearsCounted, resolution) {
  return `rank-${rankId}-${yearsCounted ? yearsCounted.join('-') : 'all'}-r${resolution}`
}

// Builds one hexagon layer: rows of { h3Cell, ... } coloured by a scale.
// schemeName tells deck.gl when the colours must be worked out again, and
// outline is how strong the white outline is, 0 to 1 (see outlineOpacity).
// selection is null, or { test, key } while an area is selected: test(cell)
// says whether a hexagon is in it (see selectionTest in area.js), and every
// other hexagon is faded (fadedColour); key names the area.
function buildHexagonLayer(id, rows, visible, schemeName, outline, selection = null) {
  const scheme = COLOUR_SCHEMES[schemeName]

  return new H3HexagonLayer({
    id,
    data: rows,
    visible,
    getHexagon: (hexagon) => hexagon.h3Cell,
    getFillColor: (hexagon) => {
      const colour = hexagonColour(hexagon, scheme)
      return selection && !selection.test(hexagon.h3Cell) ? fadedColour(colour) : colour
    },
    // DO NOT DELETE THIS. It looks redundant and is not.
    //
    // deck.gl works out each hexagon's colour once and keeps the answer in a
    // buffer on the graphics card. It only works them out again when the data
    // changes, or when updateTriggers tells it something else has. Switching
    // scale hands the layer a brand new getFillColor function, but the data
    // array is the same one as before, so without this line deck.gl keeps the
    // old colours and never calls the new function at all.
    //
    // Measured with the line removed: clicking the toggle ran getFillColor
    // zero times and the hexagons stayed blue, while the legend and the button
    // both switched correctly. With the line in place the same click ran it
    // 98,158 times, once per hexagon across all four resolutions, and the fill
    // changed. That is the whole difference.
    // The selected area's key does the same when an area is selected,
    // changed or closed.
    updateTriggers: { getFillColor: [schemeName, selection?.key ?? null] },
    // Left at 1 because the opacity that matters is the one carried per hexagon
    // by getFillColor above. deck.gl multiplies the two together, so anything
    // less than 1 here would scale every bin down at once and undo the ramp.
    opacity: 1,
    filled: true,
    // The white outline (see HEXAGON_OUTLINE), in screen pixels, faded out
    // when hexagons get too small for it.
    stroked: outline > 0,
    getLineColor: [...HEXAGON_OUTLINE.rgb, Math.round(outline * 255)],
    getLineWidth: HEXAGON_OUTLINE.widthPixels,
    lineWidthUnits: 'pixels',
    extruded: false,
    pickable: true,
    beforeId: FIRST_LABEL_LAYER_ID,
  })
}

// The outline of the hexagon whose panel is open: dark and thicker than the
// white outline of every hexagon, so it shows on any colour.
export const SELECTED_OUTLINE = { rgb: [26, 26, 26], widthPixels: 3 }

// The layer that outlines the clicked hexagon (cell, an H3 id). It has its
// own id for each hexagon, so deck.gl never carries one hexagon's geometry
// over to another (see buildLayers). It is drawn above the hexagons and is
// not pickable, so clicks go through to the hexagon under it.
export function selectedCellLayer(cell) {
  return new H3HexagonLayer({
    id: `selected-cell-${cell}`,
    data: [{ h3Cell: cell }],
    getHexagon: (hexagon) => hexagon.h3Cell,
    filled: false,
    stroked: true,
    getLineColor: SELECTED_OUTLINE.rgb,
    getLineWidth: SELECTED_OUTLINE.widthPixels,
    lineWidthUnits: 'pixels',
    extruded: false,
    pickable: false,
  })
}

// While an area is selected, every hexagon outside it keeps this share of
// its usual opacity, so the selected ones, in their normal colours, stand
// out.
export const FADED_SHARE = 0.2

// A hexagon's fill colour ([r, g, b, a]) faded for being outside the
// selected area: the same colour, FADED_SHARE of its opacity.
export function fadedColour([red, green, blue, alpha]) {
  return [red, green, blue, Math.round(alpha * FADED_SHARE)]
}

// The outline around a selected area's whole group of hexagons: a dark line
// on a wider white one, so it reads on light and dark hexagons of either
// scale (blue or orange). The box is drawn dashed in the same two colours.
export const GROUP_OUTLINE = { dark: '#1a1a1a', light: '#ffffff', darkWidth: 2.5, lightWidth: 6 }

// A box ({ west, south, east, north }) as a GeoJSON outline, for the map to
// draw the box itself, while it is dragged and once it is drawn.
export function boxOutline({ west, south, east, north }) {
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: [[west, south], [east, south], [east, north], [west, north], [west, south]],
    },
  }
}

// What the "Draw a box" button says on a single species' map, where it is
// not available yet.
export const DRAW_UNAVAILABLE = "Drawing a box is not available on a single species' map yet."

// The hint shown while drawing.
export const DRAW_HINT = 'Drag to draw a box. Esc to cancel.'

// How long after a box is drawn a click on the map is ignored, in
// milliseconds, so letting go of the mouse never also opens a hexagon.
const CLICK_AFTER_DRAW_MS = 400

// Builds every layer the map holds.
//
// All four overview resolutions from the shipped files (all years) are
// always built and handed to deck.gl, and at most one is visible: the
// current tier, and none while a species is open (also while its map is
// still loading or has failed, so the overview never shows under a species
// card) or while a year range is set. The hidden ones stay loaded on the
// graphics card without being drawn, so changing tier, closing a species
// card or going back to all years costs nothing and no hexagons are ever
// uploaded twice. The overview for a year range (overviewFiltered, with
// filteredOverview the map data from the API, or null while none has
// arrived) and the species map are the other layers, when there are any.
//
// A deck.gl layer is a description of what to draw, not the drawing itself.
// They are meant to be thrown away and made again; deck.gl keeps the real
// work, the data already on the graphics card, and finds it again by layer
// id. Holding on to an old layer object and handing the same one back is what
// caused hexagons from one zoom tier to show up under another. The outline of
// the clicked hexagon (selectedCell, or null) comes last, above the rest.
// While an area is selected (selection, see buildHexagonLayer), every
// hexagon outside it is faded.
//
// A rank's map (rankOpen, with rankData its map data from the API, or null
// while none has arrived; rankId names the rank) takes the overview's place:
// the same two views, colours and bins, for the rank's species only.
export function buildLayers({
  overviewRows, resolution, schemeName, speciesOpen, speciesData, outline = 1,
  overviewFiltered = false, filteredOverview = null, selectedCell = null,
  rankOpen = false, rankData = null, rankId = null, selection = null,
}) {
  const layers = RESOLUTIONS.map((r) => {
    const rows = overviewRows[r]
    if (!rows || rows.length === 0) {
      return null
    }
    const visible = !speciesOpen && !rankOpen && !overviewFiltered && r === resolution
    return buildHexagonLayer(overviewLayerId(r), rows, visible, schemeName, outline, selection)
  })

  if (!speciesOpen && rankOpen && rankData) {
    layers.push(buildHexagonLayer(
      rankLayerId(rankId, rankData.yearsCounted, rankData.resolution), rankData.rows, true, schemeName, outline,
      selection))
  }

  if (!speciesOpen && !rankOpen && overviewFiltered && filteredOverview) {
    layers.push(buildHexagonLayer(
      filteredOverviewLayerId(filteredOverview.yearsCounted, filteredOverview.resolution),
      filteredOverview.rows, true, schemeName, outline, selection))
  }

  if (speciesOpen && speciesData) {
    layers.push(buildHexagonLayer(
      speciesLayerId(speciesData.specieskey, speciesData.resolution),
      speciesData.rows, true, 'speciesMap', outline))
  }

  if (selectedCell) {
    layers.push(selectedCellLayer(selectedCell))
  }

  return layers.filter((layer) => layer !== null)
}

// The first word of a rank map's legend title, for each view: "Species of
// Turdus per hexagon", "Records of Turdus per hexagon".
const RANK_CAPTION_WORDS = { species: 'Species', records: 'Records' }

// The hover box for whichever map the pointer is over.
function tooltipFor(info) {
  return info.layer?.id.startsWith('species-') ? getSpeciesTooltip(info) : getTooltip(info)
}

// The box around a set of hexagons, as [[west, south], [east, north]], from
// the corners of every hexagon, so the whole of each hexagon fits.
export function boundsOfCells(rows) {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const row of rows) {
    for (const [lat, lng] of cellToBoundary(row.h3Cell)) {
      west = Math.min(west, lng)
      east = Math.max(east, lng)
      south = Math.min(south, lat)
      north = Math.max(north, lat)
    }
  }
  return [[west, south], [east, north]]
}

// The space to leave around a species when the map is fitted to it: room for
// the card panel on the right when the window is wide enough to have the
// panel beside the map, and for the legend at the bottom. On a narrow screen
// the panel covers the map anyway, so only a small margin is kept.
export function fitPadding(width, height) {
  const right = width > 2.2 * CARD_PANEL_PX ? CARD_PANEL_PX : 24
  const bottom = height > 500 ? 150 : 24
  return { top: 24, left: 24, right, bottom }
}

// No hover box over a hexagon (while drawing).
function noTooltip() {
  return null
}

// The cursor while drawing a box: a crosshair over the whole map.
function crosshairCursor() {
  return 'crosshair'
}

// The cursor otherwise: deck.gl's own, a hand that grabs while dragging.
function dragCursor({ isDragging }) {
  return isDragging ? 'grabbing' : 'grab'
}

// The box around a GeoJSON outline (see groupOutline in area.js), as
// [[west, south], [east, north]].
export function outlineBounds(outline) {
  const points = outline.geometry.coordinates.flat(2)
  const lngs = points.map(([lng]) => lng)
  const lats = points.map(([, lat]) => lat)
  return [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]]
}

// A short text naming an area (its resolution and box), to tell areas apart.
function areaId(area) {
  const { west, south, east, north } = area.box
  return `${area.res}:${west},${south},${east},${north}`
}

// The "Draw a box" button, with the controls at the top left: while
// drawing it cancels and the hint shows under it; on a single species' map
// it is greyed out, saying why when hovered (unavailable). Beside it, while
// an area exists and nothing is being drawn, "Clear area" (onClear) and,
// while the area panel is closed, "Show area" (onShow); each is null when
// its button is not shown. message (or null) is what was wrong with the
// last box drawn, with an x that calls onDismissMessage.
function DrawBoxControl({ drawing, unavailable, onToggle, message, onDismissMessage, onClear = null, onShow = null }) {
  return (
    <div className="draw-box">
      <div className="draw-box-buttons">
        <span className="draw-box-wrap" title={unavailable ? DRAW_UNAVAILABLE : undefined}>
          <button type="button" className={drawing ? 'draw-box-button drawing' : 'draw-box-button'}
            disabled={unavailable} aria-pressed={drawing} onClick={onToggle}
            aria-describedby={unavailable ? 'draw-box-unavailable' : undefined}>
            {drawing ? 'Cancel drawing' : 'Draw a box'}
          </button>
        </span>
        {onShow && !drawing && (
          <button type="button" className="draw-box-button" onClick={onShow}>Show area</button>
        )}
        {onClear && !drawing && (
          <button type="button" className="draw-box-button" onClick={onClear}>Clear area</button>
        )}
      </div>
      {unavailable && <span id="draw-box-unavailable" className="visually-hidden">{DRAW_UNAVAILABLE}</span>}
      {drawing && <p className="draw-box-hint" role="status">{DRAW_HINT}</p>}
      {message && !drawing && (
        <p className="draw-box-message" role="alert">
          <span>{message}</span>
          <button type="button" className="draw-box-message-close" onClick={onDismissMessage}
            aria-label="Dismiss this message">
            ×
          </button>
        </p>
      )}
    </div>
  )
}

// Puts the deck.gl layers onto the MapLibre map.
//
// useControl is react-map-gl's way of adding something to the map and taking
// it away again when the map goes. "interleaved: true" asks deck.gl to draw
// into the same canvas as the basemap, which is what lets a layer sit between
// the basemap's roads and its labels.
function DeckGLOverlay(props) {
  const overlay = useControl(() => new MapboxOverlay(props))

  // The overlay is created once, so it has to be told about new layers
  // whenever they change.
  overlay.setProps(props)

  return null
}

// The two buttons that choose what the overview hexagons are coloured by.
function SchemeToggle({ current, onChange }) {
  return (
    <div className="variable-toggle">
      {OVERVIEW_SCHEMES.map((name) => (
        <button
          key={name}
          type="button"
          className={name === current ? 'toggle-button selected' : 'toggle-button'}
          onClick={() => onChange(name)}
        >
          {COLOUR_SCHEMES[name].buttonLabel}
        </button>
      ))}
    </div>
  )
}

// The summary under a legend, for map data from the API, for example
// "964,801 records in 17,412 hexagons", with the years when a range was
// counted: "467,303 records in 13,278 hexagons, 2000 to 2020". The years are
// the ones of the map on screen, which while a new range loads is still the
// previous one.
export function hexesSummary(data) {
  const records = `${data.recordsShown.toLocaleString()} ${data.recordsShown === 1 ? 'record' : 'records'}`
  const hexagons = `${data.hexagonCount.toLocaleString()} ${data.hexagonCount === 1 ? 'hexagon' : 'hexagons'}`
  const years = data.yearsCounted ? `, ${yearsLabel(data.yearsCounted)}` : ''
  return `${records} in ${hexagons}${years}`
}

// The note under the summary when records with no date were left out of a
// year range, or null when none were.
export function noDateNote(data) {
  const count = data?.recordsNoYearLeftOut ?? 0
  if (!data?.yearsCounted || count === 0) {
    return null
  }
  return `${count.toLocaleString()} ${count === 1 ? 'record' : 'records'} with no date ${count === 1 ? 'is' : 'are'} not shown.`
}

// The status line under a legend for map data from the API: loading, a
// failure (what names what failed to load), or null.
function apiStatus(hexes, what) {
  if (hexes.error) {
    return `Could not load ${what}: ${hexes.error.message}`
  }
  return hexes.loading ? 'Loading hexagons...' : null
}

// The map: the overview hexagons, or, while a species card is open, that
// species' own hexagons, both switching resolution with the zoom in the same
// way, with the hexagon size label, the scale toggle (overview only) and the
// legend.
//
// overview is { rows: { 4: [...], ... }, loading, error } for the overview
// files. species is null for the overview, or { key, name, id } for the
// species whose card is open: key is the key the card was opened with (an
// old merged key works), name its scientific name for the legend, and id a
// number that changes every time a species is picked, so the map moves to
// it again even when the same species is picked twice.
//
// years is the year range for whichever map is shown (null for all years,
// when the overview uses the shipped files and the site looks as it always
// has), yearBounds is { firstYear, lastYear } from /api/meta or null, and
// onYearsChange sets a new range (see YearFilter).
//
// selectedCell is the H3 id of the hexagon whose panel is open (null for
// none), outlined on the map, and onCellClick(cell) is called when a
// hexagon is clicked, on any map.
//
// rank is null, or { rank, name, path, id } for the rank whose page is open
// (never with a species): the map is then the overview restricted to the
// rank's species, with the same toggle, colours and bins, the legend titles
// naming the rank, and a label above the map ("Showing genus Turdus") whose
// x calls onCloseRank. rankId is rankKey(rank) (route.js). id changes every
// time a rank is opened, so the map moves to it again.
//
// Drawing an area: the "Draw a box" button (not on a single species' map)
// starts drawing: the map stops panning and zooming by drag, the cursor is a
// crosshair, a hint shows, and dragging (mouse or one finger) draws the
// box; Esc or the button again cancels. Letting go calls onAreaDrawn(box,
// res) with the box (see boxFromCorners in area.js) and the resolution the
// map shows then. selectedArea is the area whose panel is open ({ box, res,
// cells, outline }, or null): every hexagon outside it is faded, at its
// own resolution whatever the zoom, and one outline is drawn around the
// whole group (outline, see groupOutline in area.js), with the box dashed.
// areaMessage (or null) is shown under the button, for a box too small or
// too big, with an x that calls onDismissAreaMessage. onClearArea (or null
// when there is no area) is the "Clear area" button beside "Draw a box",
// and onShowArea (or null when the area panel is open or covered) the
// "Show area" button that opens the area panel again.
export default function HexMap({
  overview, species, years = null, yearBounds = null, onYearsChange = () => {}, selectedCell = null,
  onCellClick = () => {}, rank = null, rankId = null, onCloseRank = () => {},
  selectedArea = null, onAreaDrawn = () => {}, areaMessage = null, onDismissAreaMessage = () => {},
  onClearArea = null, onShowArea = null,
}) {
  const mapRef = useRef(null)
  const [resolution, setResolution] = useState(() => pickResolution(INITIAL_VIEW_STATE.zoom, null))
  const [schemeName, setSchemeName] = useState(DEFAULT_SCHEME)
  // How strong the hexagon outline is, 0 to 1, from how big hexagons are on
  // screen (see outlineOpacity). Only changes in quarter steps.
  const [outline, setOutline] = useState(
    () => outlineOpacity(hexagonWidthPixels(resolution, INITIAL_VIEW_STATE.zoom)))
  const speciesHexes = useSpeciesHexes(species?.key ?? null, years, resolution)
  const speciesData = species ? speciesHexes.data : null
  // The rank's map, only asked for while a rank (and no species) is open.
  const rankOpen = Boolean(rank) && !species
  const rankHexes = useRankHexes(rankOpen ? rank : null, years, resolution)
  const rankData = rankOpen ? rankHexes.data : null
  // The overview for a year range, only asked for while no species or rank is open.
  const overviewHexes = useOverviewHexes(species || rankOpen ? null : years, resolution)
  const overviewFiltered = years !== null
  // The id of the species the map was last moved to.
  const fittedFor = useRef(null)
  // Whether the user is drawing a box, the drag so far ({ from, to }, each
  // [lng, lat], or null before it starts), and when the last box was drawn.
  const [drawing, setDrawing] = useState(false)
  const [drag, setDrag] = useState(null)
  const lastDrawn = useRef(0)
  // While an area is selected: which hexagons are in it (see
  // buildHexagonLayer), worked out once per area.
  const selection = useMemo(
    () => (selectedArea ? { test: selectionTest(selectedArea.cells, selectedArea.res), key: areaId(selectedArea) } : null),
    [selectedArea])

  const layers = useMemo(
    () => buildLayers({
      overviewRows: overview.rows, resolution, schemeName, speciesOpen: Boolean(species), speciesData, outline,
      overviewFiltered, filteredOverview: overviewHexes.data, selectedCell, rankOpen, rankData, rankId, selection,
    }),
    [overview.rows, resolution, schemeName, species, speciesData, outline, overviewFiltered, overviewHexes.data,
      selectedCell, rankOpen, rankData, rankId, selection],
  )

  // Esc cancels drawing; so does opening a single species' map, where
  // drawing is not available.
  useEffect(() => {
    if (!drawing) {
      return undefined
    }
    if (species) {
      stopDrawing()
      return undefined
    }
    function cancelOnEscape(event) {
      if (event.key === 'Escape') {
        stopDrawing()
      }
    }
    window.addEventListener('keydown', cancelOnEscape)
    return () => window.removeEventListener('keydown', cancelOnEscape)
  }, [drawing, species])

  // Ends drawing whenever the map turns to another page: another species,
  // rank, hexagon or area, whether by Back or Forward, a link, a list or a
  // panel's x. Drawing belongs to the page it was started on, so after Back
  // the map never stays waiting for a box the visitor no longer expects.
  // (A box drawn changes the area too, which ends drawing as it should.)
  const pageKey = [species?.id ?? null, rankId, selectedCell, selectedArea ? areaId(selectedArea) : null].join('|')
  useEffect(() => {
    stopDrawing()
  }, [pageKey])

  // Moves the map to a newly picked species once its first hexagons arrive:
  // fits them in view beside the card, but no closer than FIT_MAX_ZOOM, so a
  // species with one or two records does not fill the screen. Only the
  // hexagons are used to move the map; no coordinates are shown anywhere.
  // While a hexagon's panel is open the map stays on the hexagon instead
  // (the species counts as moved to, so closing the panel does not jump).
  useEffect(() => {
    const map = mapRef.current
    if (!species || !speciesData || speciesData.rows.length === 0 || !map
        || fittedFor.current === species.id) {
      return
    }
    fittedFor.current = species.id
    if (selectedCell) {
      return
    }
    const container = map.getContainer()
    map.fitBounds(boundsOfCells(speciesData.rows), {
      padding: fitPadding(container.clientWidth, container.clientHeight),
      maxZoom: FIT_MAX_ZOOM,
      duration: 900,
    })
  }, [species, speciesData, selectedCell])

  // Moves the map to a newly opened rank once its first hexagons arrive, the
  // same way as for a species (and not while a hexagon's panel is open).
  const rankFittedFor = useRef(null)
  useEffect(() => {
    const map = mapRef.current
    if (!rankOpen || !rankData || rankData.rows.length === 0 || !map || rankFittedFor.current === rank.id) {
      return
    }
    rankFittedFor.current = rank.id
    if (selectedCell) {
      return
    }
    const container = map.getContainer()
    map.fitBounds(boundsOfCells(rankData.rows), {
      padding: fitPadding(container.clientWidth, container.clientHeight),
      maxZoom: FIT_MAX_ZOOM,
      duration: 900,
    })
  }, [rankOpen, rank, rankData, selectedCell])

  // Whether the map has loaded (handleLoad has run), and the resolution of
  // the hexagons it draws now, for the effect below without making it run on
  // every zoom. A species', rank's or year range's map keeps drawing its
  // last hexagons while those of a new zoom tier load, so a hexagon clicked
  // meanwhile is of their size, not the tier's.
  const mapReady = useRef(false)
  const resolutionNow = useRef(resolution)
  const shownData = species ? speciesData : rankOpen ? rankData : overviewFiltered ? overviewHexes.data : null
  resolutionNow.current = shownData?.resolution ?? resolution

  // Shows the open hexagon as it was when it was clicked, whenever the
  // address names a hexagon the map does not show that way, for example
  // after the back button returns to its panel from another box or another
  // species' map: the map moves to it when it is off screen, and zooms to
  // hexagons of its size when the map shows another size. The outline itself
  // comes from the address (selectedCell), so the panel and the outline
  // always name the same hexagon. A clicked hexagon is on screen at its own
  // size already, so the map does not move then. Before the map has loaded,
  // handleLoad places it instead. (map.loaded() is not used here: MapLibre
  // says false while any source is still updating, which it is right after
  // Back as the area's outline goes, so the map never moved.)
  useEffect(() => {
    const map = mapRef.current
    if (!selectedCell || !map || !mapReady.current) {
      return
    }
    const [lat, lng] = cellToLatLng(selectedCell)
    const cellResolution = getResolution(selectedCell)
    const otherSize = cellResolution !== resolutionNow.current
    if (otherSize || !map.getBounds().contains([lng, lat])) {
      map.easeTo({
        center: [lng, lat], ...(otherSize ? { zoom: zoomShowingResolution(cellResolution) } : {}), duration: 900,
      })
    }
  }, [selectedCell, species?.id])

  // When the page opens with a hexagon in the address (a shared link), moves
  // the map to it, at a zoom where hexagons of its size are shown; with an
  // area, fits its whole group of hexagons (which reaches past the box) in
  // view beside the panel.
  function handleLoad() {
    mapReady.current = true
    const map = mapRef.current
    if (map && selectedArea) {
      const container = map.getContainer()
      map.fitBounds(outlineBounds(selectedArea.outline), {
        padding: fitPadding(container.clientWidth, container.clientHeight), duration: 0,
      })
      return
    }
    if (!selectedCell || !map) {
      return
    }
    const [lat, lng] = cellToLatLng(selectedCell)
    map.jumpTo({ center: [lng, lat], zoom: zoomShowingResolution(getResolution(selectedCell)) })
  }

  // Opens the panel of the hexagon under a click, on either map; not while
  // drawing a box, nor just after one is drawn (letting go of the mouse).
  function handleClick(info) {
    const cell = info.object?.h3Cell
    if (cell && !drawing && Date.now() - lastDrawn.current > CLICK_AFTER_DRAW_MS) {
      onCellClick(cell)
    }
  }

  // Starts or cancels drawing (the "Draw a box" button). Starting clears
  // any message about the last box.
  function toggleDrawing() {
    if (drawing) {
      stopDrawing()
    } else {
      onDismissAreaMessage()
      setDrawing(true)
    }
  }

  // Stops drawing without a box.
  function stopDrawing() {
    setDrawing(false)
    setDrag(null)
  }

  // The [lng, lat] under a mouse or touch event, or null for a touch with
  // more than one finger (left to the map, which is not moved meanwhile).
  function pointOf(event) {
    if (event.points && event.points.length > 1) {
      return null
    }
    return event.lngLat ? [event.lngLat.lng, event.lngLat.lat] : null
  }

  // Starts a box where the mouse button or finger goes down.
  function startBox(event) {
    const point = drawing ? pointOf(event) : null
    if (point) {
      event.preventDefault?.()
      setDrag({ from: point, to: point })
    }
  }

  // Stretches the box to where the mouse or finger is.
  function stretchBox(event) {
    const point = drag ? pointOf(event) : null
    if (point) {
      setDrag((current) => (current ? { ...current, to: point } : current))
    }
  }

  // Finishes the box where the mouse button or finger comes up, and hands
  // it on with the resolution the map shows now (see onAreaDrawn).
  function finishBox(event) {
    if (!drag) {
      return
    }
    const to = pointOf(event) ?? drag.to
    const box = boxFromCorners(drag.from, to)
    lastDrawn.current = Date.now()
    stopDrawing()
    onAreaDrawn(box, resolution)
  }

  // Called whenever the map moves. Only stores a new resolution when the tier
  // actually changes, and a new outline strength when it moves to another
  // quarter step, so panning and ordinary zooming do not redraw the page.
  function handleMove(event) {
    const { zoom } = event.viewState
    const wanted = pickResolution(zoom, resolution)
    if (wanted !== resolution) {
      setResolution(wanted)
    }
    const wantedOutline = outlineOpacity(hexagonWidthPixels(wanted, zoom))
    if (wantedOutline !== outline) {
      setOutline(wantedOutline)
    }
  }

  const overviewReady = !overview.loading && !overview.error

  let legend = null
  if (rankOpen) {
    legend = (
      <Legend
        scheme={COLOUR_SCHEMES[schemeName]}
        caption={<>{RANK_CAPTION_WORDS[schemeName]} of <RankName rank={rank.rank} name={rank.name} /> per hexagon</>}
        summary={rankData ? hexesSummary(rankData) : null}
        leftOut={noDateNote(rankData)}
        status={apiStatus(rankHexes, `the map of this ${rank.rank}`)}
        statusIsError={Boolean(rankHexes.error)}
      />
    )
  } else if (species) {
    legend = (
      <Legend
        scheme={COLOUR_SCHEMES.speciesMap}
        caption={species.name
          ? <>Records of <i>{species.name}</i> per hexagon</>
          : 'Records of this species per hexagon'}
        summary={speciesData ? hexesSummary(speciesData) : null}
        leftOut={noDateNote(speciesData)}
        status={apiStatus(speciesHexes, 'the map of this species')}
        statusIsError={Boolean(speciesHexes.error)}
      />
    )
  } else if (overviewFiltered) {
    const data = overviewHexes.data
    legend = (
      <Legend
        scheme={COLOUR_SCHEMES[schemeName]}
        caption={COLOUR_SCHEMES[schemeName].legendCaption}
        summary={data ? hexesSummary(data) : null}
        leftOut={noDateNote(data)}
        status={apiStatus(overviewHexes, 'the map for these years')}
        statusIsError={Boolean(overviewHexes.error)}
      />
    )
  } else if (overviewReady) {
    legend = (
      <Legend scheme={COLOUR_SCHEMES[schemeName]} caption={COLOUR_SCHEMES[schemeName].legendCaption} />
    )
  }

  return (
    <>
      {!species && !rankOpen && overview.error && (
        <div className="status-message">Could not load the data: {overview.error}</div>
      )}
      {!species && !rankOpen && overview.loading && <div className="status-message">Loading hexagons...</div>}

      {(species || rankOpen || overviewReady) && (
        <div className="top-left-controls">
          {rankOpen && (
            <div className="rank-showing">
              <span>Showing {rank.rank} <RankName rank={rank.rank} name={rank.name} /></span>
              <button type="button" className="rank-showing-close" onClick={onCloseRank}
                aria-label="Stop showing this rank and show all species">
                ×
              </button>
            </div>
          )}
          <div className="hex-size-label">{hexagonSizeLabel(resolution)}</div>
          {!species && <SchemeToggle current={schemeName} onChange={setSchemeName} />}
          <YearFilter years={years} bounds={yearBounds} onChange={onYearsChange} />
          <DrawBoxControl drawing={drawing} unavailable={Boolean(species)} onToggle={toggleDrawing}
            message={areaMessage} onDismissMessage={onDismissAreaMessage}
            onClear={species ? null : onClearArea} onShow={species ? null : onShowArea} />
        </div>
      )}

      {legend}

      <Map
        ref={mapRef}
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={BASEMAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        onMove={handleMove}
        onLoad={handleLoad}
        maxZoom={MAX_ZOOM}
        // While drawing, a drag draws the box instead of moving the map.
        dragPan={!drawing}
        dragRotate={!drawing}
        touchZoomRotate={!drawing}
        touchPitch={!drawing}
        doubleClickZoom={!drawing}
        boxZoom={!drawing}
        cursor={drawing ? 'crosshair' : undefined}
        onMouseDown={startBox}
        onMouseMove={stretchBox}
        onMouseUp={finishBox}
        onTouchStart={startBox}
        onTouchMove={stretchBox}
        onTouchEnd={finishBox}
      >
        <DeckGLOverlay layers={layers} interleaved={true} getTooltip={drawing ? noTooltip : tooltipFor}
          onClick={handleClick} getCursor={drawing ? crosshairCursor : dragCursor} />
        {/* The outline around the selected group of hexagons, then the box
            dashed above it; both are added again (a new key) whenever the
            area changes, so they always sit above the hexagons. */}
        {selectedArea && !drag && (
          <Source key={`group-${areaId(selectedArea)}`} id="area-group" type="geojson" data={selectedArea.outline}>
            <Layer id="area-group-edge" type="line"
              paint={{ 'line-color': GROUP_OUTLINE.light, 'line-width': GROUP_OUTLINE.lightWidth }} />
            <Layer id="area-group-line" type="line"
              paint={{ 'line-color': GROUP_OUTLINE.dark, 'line-width': GROUP_OUTLINE.darkWidth }} />
          </Source>
        )}
        {(drag || selectedArea) && (
          <Source key={drag ? 'dragging' : areaId(selectedArea)} id="area-box" type="geojson"
            data={boxOutline(drag ? boxFromCorners(drag.from, drag.to) : selectedArea.box)}>
            {/* A white edge under a dark dashed line, so the box shows on
                light and dark hexagons alike. */}
            <Layer id="area-box-edge" type="line" paint={{ 'line-color': GROUP_OUTLINE.light, 'line-width': 3 }} />
            <Layer id="area-box-line" type="line"
              paint={{ 'line-color': GROUP_OUTLINE.dark, 'line-width': 1.5, 'line-dasharray': [3, 2] }} />
          </Source>
        )}
      </Map>
    </>
  )
}
