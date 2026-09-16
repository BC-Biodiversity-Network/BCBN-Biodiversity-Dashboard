import { useEffect, useMemo, useState } from 'react'
import Map, { useControl } from 'react-map-gl/maplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'

// MapLibre ships its own stylesheet. Without it the map still draws, but the
// zoom buttons and the attribution line in the corner come out unstyled.
import 'maplibre-gl/dist/maplibre-gl.css'

import { loadHexData } from './loadHexData'

// The basemap style. Swap this one line to change providers.
const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron'

// Where the map sits when the page opens: the middle of British Columbia,
// zoomed out far enough to see the whole province.
const INITIAL_VIEW_STATE = {
  longitude: -125,
  latitude: 54.5,
  zoom: 4.5,
}

// Which hexagon file to use at which zoom level, coarsest first.
//
// A tier applies from its own minZoom up to the next tier's minZoom. So res 4
// is used below zoom 5.5, res 5 from 5.5 up to 6.5, and res 6 from 6.5 up.
//
// These came from looking at all three files side by side at zoom 4.5, 5, 5.5,
// 6, 6.5, 7 and 8 on the real basemap, not from the hexagon sizes on paper.
// What decides it is how many pixels wide a hexagon ends up. Below roughly ten
// pixels the map turns to speckle; above roughly sixty the hexagons swallow the
// towns and rivers underneath. Each tier is used over the range where it sits
// between those two.
const ZOOM_TIERS = [
  { minZoom: 0, resolution: 4 },
  { minZoom: 5.5, resolution: 5 },
  { minZoom: 6.5, resolution: 6 },
]

// How far past a threshold the zoom has to go before the tier actually changes.
//
// Without this, a zoom sitting exactly on a threshold flips back and forth
// between two tiers as the number wobbles, and the map flickers. Requiring a
// little extra movement in whichever direction you are going means the switch
// happens once and stays.
const ZOOM_DEAD_ZONE = 0.25

// Each resolution gets its own layer id.
//
// They must not share an id. deck.gl matches layers between renders by id and
// carries internal state across the match, so when one id was used for all
// three resolutions the hexagon geometry worked out for one tier was handed to
// the next one and part of the map drew at the wrong cell size.
function hexLayerId(resolution) {
  return `bc-hexagons-r${resolution}`
}

// The three resolutions, coarsest first.
const RESOLUTIONS = [4, 5, 6]

// Where the three hexagon files live, one per resolution.
const HEX_DATA_URLS = {
  4: 'data/bc_hex_r4.csv.gz',
  5: 'data/bc_hex_r5.csv.gz',
  6: 'data/bc_hex_r6.csv.gz',
}

// The five fill colours, palest to darkest, matching the project deck.
// Written as red, green, blue numbers because that is what deck.gl expects.
const COLOR_RAMP = [
  [205, 226, 251], // #cde2fb
  [158, 197, 244], // #9ec5f4
  [85, 152, 231], // #5598e7
  [37, 106, 191], // #256abf
  [24, 79, 149], // #184f95
]

// Where each colour starts, written as log10 of the occurrence count.
// 1 means 10 records, 2 means 100, 3 means 1,000 and 4 means 10,000, so each
// colour covers a tenfold jump in the count.
//
// The scale has to be logarithmic. Counts at this resolution run from 1 to
// 2,201,357, so spacing the colours evenly by count would give Vancouver the
// darkest shade and leave the other 4,461 hexagons looking identical.
//
// These numbers are a starting point and get tuned in the next round.
const COLOR_BREAKS = [1, 2, 3, 4]

// The basemap's place names start at this layer. Drawing the hexagons
// immediately before it puts them underneath every label, so city names stay
// readable through the semi-transparent fill.
const FIRST_LABEL_LAYER_ID = 'waterway_line_label'

// How see-through the hexagons are, from 0 to 1.
const HEX_OPACITY = 0.7

// Picks the fill colour for one hexagon from its occurrence count.
// Walks up the list of breakpoints and stops at the first one the count has
// not reached yet.
function getHexagonColor(occurrences) {
  const scaled = Math.log10(occurrences)

  let index = 0
  while (index < COLOR_BREAKS.length && scaled >= COLOR_BREAKS[index]) {
    index++
  }

  return COLOR_RAMP[index]
}

// Works out which tier a zoom level belongs to, ignoring the dead zone.
// Walks the list from coarsest to finest and keeps the last tier the zoom has
// reached.
function tierIndexForZoom(zoom) {
  let index = 0

  for (let i = 0; i < ZOOM_TIERS.length; i++) {
    if (zoom >= ZOOM_TIERS[i].minZoom) {
      index = i
    }
  }

  return index
}

// Decides which resolution to show at this zoom, given the one already showing.
//
// The dead zone is what stops the flicker. Moving to a finer tier needs the
// zoom to be a little past that tier's threshold; moving back to a coarser one
// needs it to be a little below the current tier's threshold. In between,
// whatever is already on screen stays there.
//
// The move is one tier at a time, which is all that is needed because zooming
// is continuous.
function pickResolution(zoom, currentResolution) {
  const wantedIndex = tierIndexForZoom(zoom)

  // Nothing showing yet, so there is no flicker to avoid.
  if (currentResolution === null) {
    return ZOOM_TIERS[wantedIndex].resolution
  }

  const currentIndex = ZOOM_TIERS.findIndex(
    (tier) => tier.resolution === currentResolution,
  )

  if (wantedIndex === currentIndex) {
    return currentResolution
  }

  if (wantedIndex > currentIndex) {
    // Zooming in. Only step up once clearly past the next threshold.
    const nextTier = ZOOM_TIERS[currentIndex + 1]
    return zoom >= nextTier.minZoom + ZOOM_DEAD_ZONE
      ? nextTier.resolution
      : currentResolution
  }

  // Zooming out. Only step down once clearly below this tier's own threshold.
  const currentTier = ZOOM_TIERS[currentIndex]
  return zoom < currentTier.minZoom - ZOOM_DEAD_ZONE
    ? ZOOM_TIERS[currentIndex - 1].resolution
    : currentResolution
}

// Builds the hexagon layer for one resolution.
//
// All three resolutions are built and handed to deck.gl together, and only the
// one for the current zoom is set visible. The other two stay loaded on the
// graphics card without being drawn, so changing tier costs nothing and no
// hexagons are ever uploaded twice.
//
// Returns null when that file has not arrived yet.
function buildHexagonLayer(resolution, rows, visible) {
  if (!rows || rows.length === 0) {
    return null
  }

  return new H3HexagonLayer({
    id: hexLayerId(resolution),
    data: rows,
    visible,
    getHexagon: (hexagon) => hexagon.h3Cell,
    getFillColor: (hexagon) => getHexagonColor(hexagon.occurrences),
    opacity: HEX_OPACITY,
    filled: true,
    stroked: false,
    extruded: false,
    pickable: true,
    beforeId: FIRST_LABEL_LAYER_ID,
  })
}

// Builds the little box that appears when the pointer is over a hexagon.
// Returning null means no hexagon is under the pointer, so no box is shown.
function getTooltip({ object }) {
  if (!object) {
    return null
  }

  return {
    html: `
      <div><strong>${object.occurrences.toLocaleString()}</strong> records</div>
      <div><strong>${object.distinctSpecies.toLocaleString()}</strong> species</div>
      <div style="opacity:0.6;margin-top:4px">${object.h3Cell}</div>
    `,
  }
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

// The whole page: a full-window map that swaps hexagon resolution as you zoom.
export default function App() {
  const [rowsByResolution, setRowsByResolution] = useState({})
  const [resolution, setResolution] = useState(() =>
    pickResolution(INITIAL_VIEW_STATE.zoom, null),
  )
  const [error, setError] = useState(null)

  // Download all three files once, when the page first appears.
  //
  // Together they are about 141 KB, which is small enough to keep in memory for
  // the whole session. Fetching them up front means zooming never waits on the
  // network. The empty list at the end tells React not to run this again.
  useEffect(() => {
    Promise.all(RESOLUTIONS.map((r) => loadHexData(HEX_DATA_URLS[r])))
      .then((loaded) => {
        const byResolution = {}
        RESOLUTIONS.forEach((r, index) => {
          byResolution[r] = loaded[index]
          console.log(`Loaded ${loaded[index].length} hexagons for res ${r}`)
        })
        setRowsByResolution(byResolution)
      })
      .catch((loadError) => {
        console.error(loadError)
        setError(loadError.message)
      })
  }, [])

  // Build all three layers, and mark only the current one visible.
  //
  // A deck.gl layer is a description of what to draw, not the drawing itself.
  // They are meant to be thrown away and made again; deck.gl keeps the real
  // work, the data already on the graphics card, and finds it again by layer
  // id. Holding on to an old layer object and handing the same one back is what
  // caused hexagons from one zoom tier to show up under another.
  //
  // So these are rebuilt whenever the tier changes, which is cheap: making a
  // layer only stores a few properties. The rows themselves are the same arrays
  // as before, so deck.gl knows the hexagons are unchanged and leaves them be.
  const layers = useMemo(() => {
    return RESOLUTIONS.map((r) =>
      buildHexagonLayer(r, rowsByResolution[r], r === resolution),
    ).filter((layer) => layer !== null)
  }, [rowsByResolution, resolution])

  // Called whenever the map moves. Only stores a new resolution when the tier
  // actually changes, so panning and ordinary zooming do not redraw the page.
  function handleMove(event) {
    const wanted = pickResolution(event.viewState.zoom, resolution)

    if (wanted !== resolution) {
      console.log(`Zoom ${event.viewState.zoom.toFixed(2)} -> res ${wanted}`)
      setResolution(wanted)
    }
  }

  const stillLoading = !error && Object.keys(rowsByResolution).length === 0

  return (
    <>
      {error && <div className="status-message">Could not load the data: {error}</div>}
      {stillLoading && <div className="status-message">Loading hexagons...</div>}

      <Map
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={BASEMAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        onMove={handleMove}
      >
        <DeckGLOverlay layers={layers} interleaved={true} getTooltip={getTooltip} />
      </Map>
    </>
  )
}
