import { useEffect, useMemo, useRef, useState } from 'react'
import Map, { useControl } from 'react-map-gl/maplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { cellToBoundary } from 'h3-js'

// MapLibre ships its own stylesheet. Without it the map still draws, but the
// zoom buttons and the attribution line in the corner come out unstyled.
import 'maplibre-gl/dist/maplibre-gl.css'

import { COLOUR_SCHEMES, HEXAGON_OUTLINE, OVERVIEW_SCHEMES, hexagonColour, outlineOpacity } from './colours'
import { getSpeciesTooltip, getTooltip } from './hexTooltip'
import Legend from './Legend'
import { useSpeciesHexes } from './speciesHexes'
import {
  FIT_MAX_ZOOM, MAX_ZOOM, RESOLUTIONS, hexagonSizeLabel, hexagonWidthPixels, pickResolution,
} from './zoomTiers'

// The basemap style. Swap this one line to change providers.
const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/bright'

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

// Builds one hexagon layer: rows of { h3Cell, ... } coloured by a scale.
// schemeName tells deck.gl when the colours must be worked out again, and
// outline is how strong the white outline is, 0 to 1 (see outlineOpacity).
function buildHexagonLayer(id, rows, visible, schemeName, outline) {
  const scheme = COLOUR_SCHEMES[schemeName]

  return new H3HexagonLayer({
    id,
    data: rows,
    visible,
    getHexagon: (hexagon) => hexagon.h3Cell,
    getFillColor: (hexagon) => hexagonColour(hexagon, scheme),
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
    updateTriggers: { getFillColor: schemeName },
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

// Builds every layer the map holds.
//
// All four overview resolutions are always built and handed to deck.gl, and
// at most one is visible: the current tier, and none while a species is
// open (also while its map is still loading or has failed, so the overview
// never shows under a species card). The hidden ones stay loaded on the
// graphics card without being drawn, so changing tier, or closing a species
// card, costs nothing and no hexagons are ever uploaded twice. The species map, when there is one, is
// the one other layer.
//
// A deck.gl layer is a description of what to draw, not the drawing itself.
// They are meant to be thrown away and made again; deck.gl keeps the real
// work, the data already on the graphics card, and finds it again by layer
// id. Holding on to an old layer object and handing the same one back is what
// caused hexagons from one zoom tier to show up under another.
export function buildLayers({ overviewRows, resolution, schemeName, speciesOpen, speciesData, outline = 1 }) {
  const layers = RESOLUTIONS.map((r) => {
    const rows = overviewRows[r]
    if (!rows || rows.length === 0) {
      return null
    }
    return buildHexagonLayer(overviewLayerId(r), rows, !speciesOpen && r === resolution, schemeName, outline)
  })

  if (speciesOpen && speciesData) {
    layers.push(buildHexagonLayer(
      speciesLayerId(speciesData.specieskey, speciesData.resolution),
      speciesData.rows, true, 'speciesMap', outline))
  }

  return layers.filter((layer) => layer !== null)
}

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

// The summary under the species map's legend, for example
// "964,801 records in 17,412 hexagons".
export function speciesSummary(data) {
  const records = `${data.recordsShown.toLocaleString()} ${data.recordsShown === 1 ? 'record' : 'records'}`
  const hexagons = `${data.hexagonCount.toLocaleString()} ${data.hexagonCount === 1 ? 'hexagon' : 'hexagons'}`
  return `${records} in ${hexagons}`
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
export default function HexMap({ overview, species }) {
  const mapRef = useRef(null)
  const [resolution, setResolution] = useState(() => pickResolution(INITIAL_VIEW_STATE.zoom, null))
  const [schemeName, setSchemeName] = useState(DEFAULT_SCHEME)
  // How strong the hexagon outline is, 0 to 1, from how big hexagons are on
  // screen (see outlineOpacity). Only changes in quarter steps.
  const [outline, setOutline] = useState(
    () => outlineOpacity(hexagonWidthPixels(resolution, INITIAL_VIEW_STATE.zoom)))
  const speciesHexes = useSpeciesHexes(species?.key ?? null, resolution)
  const speciesData = species ? speciesHexes.data : null
  // The id of the species the map was last moved to.
  const fittedFor = useRef(null)

  const layers = useMemo(
    () => buildLayers({
      overviewRows: overview.rows, resolution, schemeName, speciesOpen: Boolean(species), speciesData, outline,
    }),
    [overview.rows, resolution, schemeName, species, speciesData, outline],
  )

  // Moves the map to a newly picked species once its first hexagons arrive:
  // fits them in view beside the card, but no closer than FIT_MAX_ZOOM, so a
  // species with one or two records does not fill the screen. Only the
  // hexagons are used to move the map; no coordinates are shown anywhere.
  useEffect(() => {
    const map = mapRef.current
    if (!species || !speciesData || speciesData.rows.length === 0 || !map
        || fittedFor.current === species.id) {
      return
    }
    fittedFor.current = species.id
    const container = map.getContainer()
    map.fitBounds(boundsOfCells(speciesData.rows), {
      padding: fitPadding(container.clientWidth, container.clientHeight),
      maxZoom: FIT_MAX_ZOOM,
      duration: 900,
    })
  }, [species, speciesData])

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
  if (species) {
    let status = null
    if (speciesHexes.error) {
      status = `Could not load the map of this species: ${speciesHexes.error.message}`
    } else if (speciesHexes.loading) {
      status = 'Loading hexagons...'
    }
    legend = (
      <Legend
        scheme={COLOUR_SCHEMES.speciesMap}
        caption={species.name
          ? <>Records of <i>{species.name}</i> per hexagon</>
          : 'Records of this species per hexagon'}
        summary={speciesData ? speciesSummary(speciesData) : null}
        status={status}
        statusIsError={Boolean(speciesHexes.error)}
      />
    )
  } else if (overviewReady) {
    legend = (
      <Legend scheme={COLOUR_SCHEMES[schemeName]} caption={COLOUR_SCHEMES[schemeName].legendCaption} />
    )
  }

  return (
    <>
      {!species && overview.error && (
        <div className="status-message">Could not load the data: {overview.error}</div>
      )}
      {!species && overview.loading && <div className="status-message">Loading hexagons...</div>}

      {(species || overviewReady) && (
        <div className="top-left-controls">
          <div className="hex-size-label">{hexagonSizeLabel(resolution)}</div>
          {!species && <SchemeToggle current={schemeName} onChange={setSchemeName} />}
        </div>
      )}

      {legend}

      <Map
        ref={mapRef}
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={BASEMAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        onMove={handleMove}
        maxZoom={MAX_ZOOM}
      >
        <DeckGLOverlay layers={layers} interleaved={true} getTooltip={tooltipFor} />
      </Map>
    </>
  )
}
