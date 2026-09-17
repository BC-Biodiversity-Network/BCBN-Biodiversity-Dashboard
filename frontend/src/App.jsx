import { useEffect, useMemo, useState } from 'react'
import Map, { useControl } from 'react-map-gl/maplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { getHexagonEdgeLengthAvg } from 'h3-js'

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

// The four resolutions, coarsest first.
const RESOLUTIONS = [4, 5, 6, 7]

// Switch to the finer tier once a hexagon grows past this many pixels
// across. This one number sets every threshold. Larger means hexagons
// get bigger before switching, so the map reads coarser and less busy.
const SWITCH_AT_PX = 80

// How many metres of ground one pixel covers at zoom 0, on the equator.
//
// The familiar figure for this is 156543.03, but that is for maps built from
// 256-pixel tiles. MapLibre uses 512-pixel tiles, so at the same zoom number
// the world is twice as wide in pixels and each pixel covers half as much
// ground. Using the 256 figure here makes every hexagon come out half its real
// size on screen. Measured against the map's own projection this value is
// right to within about one percent, and that last one percent is real
// variation in H3 cell sizes rather than an error in the arithmetic.
const METRES_PER_PIXEL_AT_ZOOM_0 = 78271.52

// The latitude the thresholds are worked out for: the middle of BC.
//
// A pixel covers less ground the further north you go, so a hexagon looks
// bigger in the north than in the south at the same zoom. If the thresholds
// followed the live map centre, the tier would change while panning north or
// south at a fixed zoom, which is confusing. BC spans 48 to 60 degrees, enough
// to move a threshold by about 0.4 of a zoom level. Fixing the latitude here
// costs a little accuracy at the top and bottom of the province and buys a
// threshold that never moves under the user.
const REFERENCE_LATITUDE = 54.5

// How wide a hexagon of this resolution is, corner to corner, in metres.
// A regular hexagon measures two edge lengths across its widest point.
function hexagonWidthMetres(resolution) {
  return 2 * getHexagonEdgeLengthAvg(resolution, 'm')
}

// The zoom level at which a hexagon of this resolution appears this many
// pixels wide. This is the pixel formula turned around to solve for zoom.
function zoomAtWidth(resolution, widthPx) {
  const metresPerPixel =
    METRES_PER_PIXEL_AT_ZOOM_0 * Math.cos((REFERENCE_LATITUDE * Math.PI) / 180)

  return Math.log2((widthPx * metresPerPixel) / hexagonWidthMetres(resolution))
}

// The text shown in the corner label for one resolution, for example
// "Hexagons ~52 km". The number is the width corner to corner, rounded to
// whole kilometres, worked out from the resolution rather than written down.
function hexagonSizeLabel(resolution) {
  const widthKm = hexagonWidthMetres(resolution) / 1000

  return `Hexagons ~${Math.round(widthKm)} km`
}

// Which hexagon file to use at which zoom level, coarsest first.
//
// A tier applies from its own minZoom up to the next tier's minZoom. Each one
// takes over at the zoom where the tier before it has grown to SWITCH_AT_PX
// across, so every threshold moves together when that one number is changed.
//
// Because consecutive H3 resolutions differ in width by the square root of
// seven, about 2.65, a tier is entered at roughly SWITCH_AT_PX / 2.65 pixels
// and left at SWITCH_AT_PX. At 80 that is 30 pixels entering, 80 leaving.
const ZOOM_TIERS = RESOLUTIONS.map((resolution, index) => ({
  resolution,
  // The coarsest tier has to cover everything below it, so it starts at zero.
  minZoom: index === 0 ? 0 : zoomAtWidth(RESOLUTIONS[index - 1], SWITCH_AT_PX),
}))

// How far the map is allowed to zoom in.
//
// Past this point a res 7 hexagon is twice SWITCH_AT_PX across and still
// growing, so zooming further only magnifies the same aggregate instead of
// showing anything new. Going deeper would mean drawing individual occurrence
// records rather than hexagons, and that needs a query API that does not exist
// yet. This cap is the edge of what the aggregates can usefully show, not an
// arbitrary limit.
const MAX_ZOOM = zoomAtWidth(
  RESOLUTIONS[RESOLUTIONS.length - 1],
  2 * SWITCH_AT_PX,
)

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

// Where the four hexagon files live, one per resolution.
//
// All four are fetched at startup. Together they are about 469 KB, which is
// less than a single photograph, so fetching only the one the current zoom
// needs would add code and a loading pause for no real saving. Please do not
// add lazy loading here without a measurement showing it is worth it.
const HEX_DATA_URLS = {
  4: 'data/bc_hex_r4.csv.gz',
  5: 'data/bc_hex_r5.csv.gz',
  6: 'data/bc_hex_r6.csv.gz',
  7: 'data/bc_hex_r7.csv.gz',
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
// zoom to be a little past the next threshold; moving back to a coarser one
// needs it to be a little below the current tier's threshold. In between,
// whatever is already on screen stays there.
//
// Once the zoom has cleared that dead zone it goes straight to whichever tier
// the new zoom belongs to, however many tiers away that is. Dragging the zoom
// slowly sends a stream of small movements and would step through the tiers
// anyway, but a jump straight to a new zoom arrives as a single movement, and
// stepping one tier at a time would leave the map showing the wrong one.
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
    // Zooming in. Only move once clearly past the next threshold up.
    const nextTier = ZOOM_TIERS[currentIndex + 1]
    return zoom >= nextTier.minZoom + ZOOM_DEAD_ZONE
      ? ZOOM_TIERS[wantedIndex].resolution
      : currentResolution
  }

  // Zooming out. Only move once clearly below this tier's own threshold.
  const currentTier = ZOOM_TIERS[currentIndex]
  return zoom < currentTier.minZoom - ZOOM_DEAD_ZONE
    ? ZOOM_TIERS[wantedIndex].resolution
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

      {!stillLoading && !error && (
        <div className="hex-size-label">{hexagonSizeLabel(resolution)}</div>
      )}

      <Map
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={BASEMAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        onMove={handleMove}
        maxZoom={MAX_ZOOM}
      >
        <DeckGLOverlay layers={layers} interleaved={true} getTooltip={getTooltip} />
      </Map>
    </>
  )
}
