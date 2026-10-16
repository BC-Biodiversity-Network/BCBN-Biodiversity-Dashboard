import { getHexagonEdgeLengthAvg } from 'h3-js'

// Which hexagon resolution the map shows at which zoom, shared by the
// overview map and the species map so both switch at exactly the same
// places. Moved here unchanged from App.jsx.

// The four resolutions, coarsest first.
export const RESOLUTIONS = [4, 5, 6, 7]

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
export function hexagonWidthMetres(resolution) {
  return 2 * getHexagonEdgeLengthAvg(resolution, 'm')
}

// The zoom level at which a hexagon of this resolution appears this many
// pixels wide. This is the pixel formula turned around to solve for zoom.
export function zoomAtWidth(resolution, widthPx) {
  const metresPerPixel =
    METRES_PER_PIXEL_AT_ZOOM_0 * Math.cos((REFERENCE_LATITUDE * Math.PI) / 180)

  return Math.log2((widthPx * metresPerPixel) / hexagonWidthMetres(resolution))
}

// How many pixels wide a hexagon of this resolution appears at this zoom
// (at REFERENCE_LATITUDE). The opposite of zoomAtWidth.
export function hexagonWidthPixels(resolution, zoom) {
  const metresPerPixel =
    (METRES_PER_PIXEL_AT_ZOOM_0 * Math.cos((REFERENCE_LATITUDE * Math.PI) / 180)) / 2 ** zoom

  return hexagonWidthMetres(resolution) / metresPerPixel
}

// The text shown in the corner label for one resolution, for example
// "Hexagons ~52 km". The number is the width corner to corner, rounded to
// whole kilometres, worked out from the resolution rather than written down.
export function hexagonSizeLabel(resolution) {
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
export const ZOOM_TIERS = RESOLUTIONS.map((resolution, index) => ({
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
export const MAX_ZOOM = zoomAtWidth(
  RESOLUTIONS[RESOLUTIONS.length - 1],
  2 * SWITCH_AT_PX,
)

// How far past a threshold the zoom has to go before the tier actually changes.
//
// Without this, a zoom sitting exactly on a threshold flips back and forth
// between two tiers as the number wobbles, and the map flickers. Requiring a
// little extra movement in whichever direction you are going means the switch
// happens once and stays.
export const ZOOM_DEAD_ZONE = 0.25

// How far the map may zoom in when it moves to a species. A species with one
// or two records would otherwise fill the screen with one hexagon. At zoom 8
// the map shows res 6 hexagons (about 7 km across) at about 42 pixels, with
// about 230 km of ground across a 1280-pixel screen: close enough to see
// where the records are, far enough to see where that is.
export const FIT_MAX_ZOOM = 8

// Works out which tier a zoom level belongs to, ignoring the dead zone.
// Walks the list from coarsest to finest and keeps the last tier the zoom has
// reached.
export function tierIndexForZoom(zoom) {
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
export function pickResolution(zoom, currentResolution) {
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
