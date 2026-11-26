import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The real map (MapLibre) and layers (deck.gl) need a graphics card, which
// the test page does not have. These stand-ins record what the map is given
// instead: the layers handed to deck.gl, the map's onMove handler (to pretend
// the user zoomed) and every fitBounds call, the overlay's click handler, the
// map's onLoad handler, and every easeTo and jumpTo call. bounds is the part
// of the world on screen, [[west, south], [east, north]]. props is every
// prop the map was last given (its mouse and touch handlers, whether a drag
// pans it, its cursor), sources the GeoJSON of each line it draws, by
// source id (the area's shapes, "area-box"), and mapLayers each MapLibre
// layer's props, by layer id.
const mapState = vi.hoisted(() => ({
  layers: [], getTooltip: null, onMove: null, fitBounds: null, onClick: null, onLoad: null, easeTo: null,
  jumpTo: null, bounds: [[-140, 47], [-113, 61]], props: {}, sources: {}, mapLayers: {}, loaded: true,
}))

vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react')
  // A stand-in for the map: renders its children and offers fitBounds.
  const Map = React.forwardRef(function Map(props, ref) {
    React.useImperativeHandle(ref, () => ({
      fitBounds: (...args) => mapState.fitBounds(...args),
      getContainer: () => ({ clientWidth: 1280, clientHeight: 800 }),
      loaded: () => mapState.loaded,
      getBounds: () => ({
        contains: ([lng, lat]) => lng >= mapState.bounds[0][0] && lng <= mapState.bounds[1][0]
          && lat >= mapState.bounds[0][1] && lat <= mapState.bounds[1][1],
      }),
      easeTo: (...args) => mapState.easeTo(...args),
      jumpTo: (...args) => mapState.jumpTo(...args),
      // 100 pixels to a degree, north up.
      project: ([lng, lat]) => ({ x: lng * 100, y: -lat * 100 }),
    }))
    mapState.onMove = props.onMove
    mapState.onLoad = props.onLoad
    mapState.props = props
    mapState.sources = {}
    mapState.mapLayers = {}
    return React.createElement('div', { 'data-testid': 'map' }, props.children)
  })
  // Stand-ins for a GeoJSON source and its layer: the source records its
  // data, the layer its props.
  function Source({ id, data, children }) {
    mapState.sources[id] = data
    return React.createElement(React.Fragment, null, children)
  }
  function Layer(props) {
    mapState.mapLayers[props.id] = props
    return null
  }
  // A stand-in for useControl: keeps the overlay's latest layers.
  function useControl() {
    return {
      setProps: (props) => {
        mapState.layers = props.layers
        mapState.getTooltip = props.getTooltip
        mapState.onClick = props.onClick
      },
    }
  }
  return { default: Map, Layer, Source, useControl }
})
vi.mock('@deck.gl/mapbox', () => ({ MapboxOverlay: class {} }))
vi.mock('@deck.gl/geo-layers', () => ({
  // A stand-in layer that only keeps what it was given.
  H3HexagonLayer: class {
    constructor(props) {
      this.props = props
      this.id = props.id
    }
  },
}))

// Imported after the stand-ins above, which vitest puts in place first.
import { cellToLatLng, latLngToCell } from 'h3-js'

import { boxCells } from './area'
import { SHAPE_OUTLINE, shapesBounds } from './areaLayers'
import { DRAW_HELP, DRAW_HINT, DRAW_UNAVAILABLE, polygonHint } from './AreaOfInterest'
import { CROSSING_POINT, POLYGON_HINT } from './areaPolygon'
import { boxShapeOf, readAreaText } from './areaShapes'
import { COLOUR_SCHEMES, hexagonColour, outlineOpacity } from './colours'
import HexMap, {
  AREA_FIT_MIN_MAP_PX, FADED_SHARE, areaFitPadding, fitPadding, overviewLayerId, panelsCover,
} from './HexMap'
import { hexagonWidthPixels, pickResolution, zoomShowingResolution } from './zoomTiers'

// One overview hexagon per resolution, real H3 cells in BC.
const OVERVIEW = {
  rows: {
    4: [{ h3Cell: '8428c9bffffffff', occurrences: 120, distinctSpecies: 40 }],
    5: [{ h3Cell: '8528c9b7fffffff', occurrences: 50, distinctSpecies: 20 }],
    6: [{ h3Cell: '8628c9b47ffffff', occurrences: 10, distinctSpecies: 5 }],
    7: [{ h3Cell: '8728c9b40ffffff', occurrences: 3, distinctSpecies: 2 }],
  },
  loading: false,
  error: null,
}

const ROBIN = { key: '59PMB', name: 'Turdus migratorius', id: 1 }

// A pretend API answer for a species map at one resolution.
function hexes(specieskey, res, cells, occurrences) {
  return {
    specieskey, res, cells, occurrences, hexagon_count: cells.length,
    records_shown: occurrences.reduce((a, b) => a + b, 0),
  }
}

// A pretend fetch that answers species map requests with answerFor(url),
// and records the URLs asked for.
function stubHexes(answerFor) {
  const fetchMock = vi.fn(async (url) => {
    const { status = 200, body } = answerFor(url)
    return { ok: status < 300, status, json: async () => body }
  })
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls.map(([url]) => url)
}

// The ids of the layers deck.gl was last given that are visible.
function visibleLayers() {
  return mapState.layers.filter((layer) => layer.props.visible).map((layer) => layer.id)
}

// Lets waiting answers arrive and the page catch up.
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

beforeEach(() => {
  mapState.fitBounds = vi.fn()
  mapState.easeTo = vi.fn()
  mapState.jumpTo = vi.fn()
  mapState.bounds = [[-140, 47], [-113, 61]]
  mapState.loaded = true
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the species map', () => {
  it('replaces the overview while a species is open, and restores it after', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff', '842800bffffffff'], [964000, 801]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r4'])
    expect(screen.getByText('Distinct species per hexagon')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Records' })).toBeTruthy()

    rerender(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    expect(visibleLayers()).toEqual(['species-59PMB-r4'])
    // The overview layers stay on hand (hidden), so closing costs nothing.
    expect(mapState.layers.map((layer) => layer.id)).toContain('bc-hexagons-r7')
    expect(screen.queryByRole('button', { name: 'Records' })).toBeNull()

    rerender(<HexMap overview={OVERVIEW} species={null} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r4'])
    expect(screen.getByText('Distinct species per hexagon')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Records' })).toBeTruthy()
  })

  it('shows the legend for the species: what is shown, the bins, the summary and the note', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff', '842800bffffffff'], [964000, 801]) }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    const legend = document.querySelector('.legend')
    expect(legend.querySelector('.legend-caption').textContent).toBe('Records of Turdus migratorius per hexagon')
    expect(legend.querySelector('.legend-caption i').textContent).toBe('Turdus migratorius')
    expect([...legend.querySelectorAll('.legend-edge')].map((el) => el.textContent))
      .toEqual(['1', '3', '10', '30', '100', '300', '1K', '3K+'])
    expect(legend.querySelectorAll('.legend-swatch')).toHaveLength(8)
    // The swatches are the map's own colours with the map's own opacity:
    // the palest pink at 0.85, the darkest purple at 0.95.
    const swatches = [...legend.querySelectorAll('.legend-swatch')].map((el) => el.style.background)
    expect(swatches[0]).toBe('rgba(228, 108, 159, 0.851)')
    expect(swatches[7]).toBe('rgba(42, 12, 63, 0.949)')
    expect(legend.querySelector('.legend-summary').textContent).toBe('964,801 records in 2 hexagons')
    expect(legend.querySelector('.legend-note').textContent).toContain(
      'These maps show where people have recorded wildlife, not where wildlife is.')
  })

  it('asks for the resolution of the current tier, once per tier', async () => {
    const urls = stubHexes((url) => {
      const res = Number(url.split('res=')[1])
      return { body: hexes('59PMB', res, ['8428c9bffffffff'], [res]) }
    })
    render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    expect(urls()).toEqual(['/api/species/59PMB/hexes?res=4'])

    act(() => mapState.onMove({ viewState: { zoom: 7 } }))
    await settle()
    act(() => mapState.onMove({ viewState: { zoom: 9.5 } }))
    await settle()
    act(() => mapState.onMove({ viewState: { zoom: 4 } }))
    await settle()
    expect(urls()).toEqual([
      '/api/species/59PMB/hexes?res=4',
      '/api/species/59PMB/hexes?res=5',
      '/api/species/59PMB/hexes?res=7',
    ])
    expect(visibleLayers()).toEqual(['species-59PMB-r4'])
    expect(screen.getByText('Hexagons ~52 km')).toBeTruthy()
  })

  it('moves the map to the species once, beside the card and no closer than zoom 8', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
    const [bounds, options] = mapState.fitBounds.mock.calls[0]
    expect(options).toMatchObject({ maxZoom: 8, padding: { right: 416, left: 24, top: 24, bottom: 150 } })
    const [[west, south], [east, north]] = bounds
    expect(west).toBeLessThan(east)
    expect(south).toBeLessThan(north)
    // Re-drawing does not move the map again; picking the species again does.
    rerender(<HexMap overview={OVERVIEW} species={{ ...ROBIN }} />)
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
    rerender(<HexMap overview={OVERVIEW} species={{ ...ROBIN, id: 2 }} />)
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(2)
  })

  it('works with an old merged key', async () => {
    const urls = stubHexes(() => ({ body: hexes('79DVG', 4, ['8428c9bffffffff'], [714]) }))
    render(<HexMap overview={OVERVIEW} species={{ key: 'VM8MB', name: 'Salix scouleriana', id: 1 }} />)
    await settle()
    expect(urls()).toEqual(['/api/species/VM8MB/hexes?res=4'])
    expect(visibleLayers()).toEqual(['species-79DVG-r4'])
  })

  it('says it is loading, and says clearly when the map fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    const { unmount } = render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    expect(screen.getByRole('status').textContent).toBe('Loading hexagons...')
    expect(visibleLayers()).toEqual([])
    unmount()

    stubHexes(() => ({ status: 500, body: { detail: 'Internal Server Error' } }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    const status = screen.getByRole('status')
    expect(status.textContent).toBe('Could not load the map of this species: Internal Server Error')
    expect(status.className).toContain('error')
    expect(visibleLayers()).toEqual([])
  })

  it('shows the records of this species in the hover box, with no cell id', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    const species = mapState.getTooltip({ layer: { id: 'species-59PMB-r4' }, object: { h3Cell: '8428c9bffffffff', occurrences: 1 } })
    expect(species.html).toContain('<strong>1</strong> record of this species')
    expect(species.html).not.toContain('8428c9bffffffff')
    const overview = mapState.getTooltip({ layer: { id: 'bc-hexagons-r4' }, object: OVERVIEW.rows[4][0] })
    expect(overview.html).toContain('records')
    expect(overview.html).toContain('species')
  })
})

describe('the hexagon outlines', () => {
  it('draws a 1-pixel white outline around every hexagon, on the overview and the species map', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    for (const layer of mapState.layers) {
      expect(layer.props).toMatchObject({
        stroked: true, getLineColor: [255, 255, 255, 255], getLineWidth: 1, lineWidthUnits: 'pixels',
      })
    }
    rerender(<HexMap overview={OVERVIEW} species={ROBIN} />)
    await settle()
    const species = mapState.layers.find((layer) => layer.id === 'species-59PMB-r4')
    expect(species.props).toMatchObject({ stroked: true, getLineColor: [255, 255, 255, 255], getLineWidth: 1 })
  })

  it('fades the outline out when zoomed so far out that the hexagons get small', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    const lineColour = () => mapState.layers[0].props.getLineColor
    // Zoom 4.26: resolution 4 hexagons about 22 pixels wide, a half-strength line.
    expect(hexagonWidthPixels(4, 4.26)).toBeCloseTo(22, 0)
    act(() => mapState.onMove({ viewState: { zoom: 4.26 } }))
    expect(lineColour()).toEqual([255, 255, 255, 128])
    // Zoom 3.85: about 16.5 pixels wide, under 20, so no outline at all.
    act(() => mapState.onMove({ viewState: { zoom: 3.85 } }))
    expect(mapState.layers[0].props.stroked).toBe(false)
    // Back at the opening zoom it is fully white again.
    act(() => mapState.onMove({ viewState: { zoom: 4.5 } }))
    expect(lineColour()).toEqual([255, 255, 255, 255])
  })
})

describe('the overview legends', () => {
  it('shows the 8 species bins, and the 7 records bins after the toggle', async () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    const edges = () => [...document.querySelectorAll('.legend-edge')].map((el) => el.textContent)
    expect(edges()).toEqual(['1', '3', '10', '30', '100', '300', '1K', '3K+'])
    // The overview swatches carry the same opacity as the map, from 0.85.
    expect(document.querySelector('.legend-swatch').style.background).toBe('rgba(205, 226, 251, 0.851)')
    act(() => screen.getByRole('button', { name: 'Records' }).click())
    expect(screen.getByText('Occurrence records per hexagon')).toBeTruthy()
    expect(edges()).toEqual(['1', '10', '100', '1K', '10K', '100K', '1M+'])
    expect(document.querySelectorAll('.legend-swatch')).toHaveLength(7)
  })
})

describe('a year range', () => {
  const YEARS = { from: 2000, to: 2020 }

  // A pretend overview answer from the API for 2000 to 2020.
  function overviewAnswer(res) {
    return {
      res, year_from: 2000, year_to: 2020, years_counted: [2000, 2020],
      hexagon_count: 2, records_shown: 467303, records_total: 40785214,
      records_no_year_left_out: 37966, records_outside_years: 40279945,
      cells: ['8428c9bffffffff', '842800bffffffff'], occurrences: [467000, 303], distinct_species: [900, 12],
    }
  }

  // A pretend API: overview and species answers for 2000 to 2020.
  function stubYearsApi() {
    return stubHexes((url) => (url.startsWith('/api/overview/')
      ? { body: overviewAnswer(Number(url.match(/res=(\d)/)[1])) }
      : { body: { ...hexes('59PMB', 4, ['8428c9bffffffff'], [1200]), years_counted: [2000, 2020], records_no_year_left_out: 1 } }))
  }

  it('asks for nothing with all years: the overview comes from the shipped files', async () => {
    const urls = stubYearsApi()
    render(<HexMap overview={OVERVIEW} species={null} years={null} />)
    await settle()
    expect(urls()).toEqual([])
    expect(visibleLayers()).toEqual(['bc-hexagons-r4'])
    expect(document.querySelector('.legend-summary')).toBeNull()
  })

  it('shows the overview for the range from the API, with the range, totals and the no-date note', async () => {
    const urls = stubYearsApi()
    render(<HexMap overview={OVERVIEW} species={null} years={YEARS} />)
    await settle()
    expect(urls()).toEqual(['/api/overview/hexes?res=4&year_from=2000&year_to=2020'])
    expect(visibleLayers()).toEqual(['overview-2000-2020-r4'])
    expect(document.querySelector('.legend-summary').textContent).toBe('467,303 records in 2 hexagons, 2000 to 2020')
    expect(document.querySelector('.legend-left-out').textContent).toBe('37,966 records with no date are not shown.')
    // The same answer colours both scales; the fixed bins stay.
    const layer = () => mapState.layers.find((l) => l.id === 'overview-2000-2020-r4')
    expect(layer().props.updateTriggers.getFillColor).toEqual(['species', null])
    act(() => screen.getByRole('button', { name: 'Records' }).click())
    expect(layer().props.updateTriggers.getFillColor).toEqual(['records', null])
    expect([...document.querySelectorAll('.legend-edge')].map((el) => el.textContent))
      .toEqual(['1', '10', '100', '1K', '10K', '100K', '1M+'])
    expect(urls()).toHaveLength(1)
  })

  it('maps a species for the range, and keeps the range on the overview when the card closes', async () => {
    const urls = stubYearsApi()
    const { rerender } = render(<HexMap overview={OVERVIEW} species={ROBIN} years={YEARS} />)
    await settle()
    expect(urls()).toEqual(['/api/species/59PMB/hexes?res=4&year_from=2000&year_to=2020'])
    expect(document.querySelector('.legend-summary').textContent).toBe('1,200 records in 1 hexagon, 2000 to 2020')
    expect(document.querySelector('.legend-left-out').textContent).toBe('1 record with no date is not shown.')

    rerender(<HexMap overview={OVERVIEW} species={null} years={YEARS} />)
    await settle()
    expect(urls()[1]).toBe('/api/overview/hexes?res=4&year_from=2000&year_to=2020')
    expect(visibleLayers()).toEqual(['overview-2000-2020-r4'])
  })

  it('says it is loading, and says clearly when the range cannot be loaded', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    stubHexes(() => ({ status: 500, body: { detail: 'Internal Server Error' } }))
    render(<HexMap overview={OVERVIEW} species={null} years={YEARS} />)
    expect(screen.getByRole('status').textContent).toBe('Loading hexagons...')
    expect(visibleLayers()).toEqual([])
    await settle()
    expect(screen.getByRole('status').textContent).toBe('Could not load the map for these years: Internal Server Error')
    // The all-years overview is not shown in its place.
    expect(visibleLayers()).toEqual([])
  })

  it('shows the year control under the scale toggle, then the area of interest button', () => {
    render(<HexMap overview={OVERVIEW} species={null} years={null} yearBounds={{ firstYear: 1784, lastYear: 2026 }} />)
    const controls = document.querySelector('.top-left-controls')
    expect([...controls.children].map((el) => el.className))
      .toEqual(['hex-size-label', 'variable-toggle', 'year-filter', 'draw-box'])
  })
})

describe('the clicked hexagon', () => {
  it('opens the panel of the hexagon under a click, on the overview and on a species map', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const onCellClick = vi.fn()
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} onCellClick={onCellClick} />)
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    expect(onCellClick).toHaveBeenLastCalledWith('8428c9bffffffff')
    act(() => mapState.onClick({ object: null }))
    expect(onCellClick).toHaveBeenCalledTimes(1)
    rerender(<HexMap overview={OVERVIEW} species={ROBIN} onCellClick={onCellClick} />)
    await settle()
    act(() => mapState.onClick({ object: { h3Cell: '8428c9bffffffff', occurrences: 3 } }))
    expect(onCellClick).toHaveBeenCalledTimes(2)
  })

  it('outlines the open hexagon above the others, dark and 3 pixels wide, and never in the way of a click', () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedCell="8528c9b7fffffff" />)
    const outline = mapState.layers[mapState.layers.length - 1]
    expect(outline.id).toBe('selected-cell-8528c9b7fffffff')
    expect(outline.props).toMatchObject({
      data: [{ h3Cell: '8528c9b7fffffff' }], filled: false, stroked: true, getLineColor: [26, 26, 26],
      getLineWidth: 3, lineWidthUnits: 'pixels', pickable: false,
    })
  })

  it('draws no outline with no hexagon open', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    expect(mapState.layers.some((layer) => layer.id.startsWith('selected-cell-'))).toBe(false)
  })

  it('opens a hexagon from a shared link at a zoom that shows hexagons of its size', () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedCell="8728c9b40ffffff" />)
    act(() => mapState.onLoad())
    const [{ center, zoom }] = mapState.jumpTo.mock.calls[0]
    const [lat, lng] = cellToLatLng('8728c9b40ffffff')
    expect(center).toEqual([lng, lat])
    expect(zoom).toBe(zoomShowingResolution(7))
  })

  it('shows the open hexagon from the address as when it was clicked: on screen, at its own size', () => {
    // The map shows the resolution of its first zoom; three hexagons of it,
    // two on screen and one off it, and one of a finer resolution on screen.
    const shown = pickResolution(4.5, null)
    const onScreen = latLngToCell(54.5, -125, shown)
    const alsoOnScreen = latLngToCell(50.7, -120.3, shown)
    const offScreen = latLngToCell(59.5, -135, shown)
    const finer = latLngToCell(50.7, -120.3, shown + 2)
    mapState.bounds = [[-130, 49], [-118, 57]]
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedCell={onScreen} />)
    // Before the map has loaded, handleLoad places it instead.
    expect(mapState.easeTo).not.toHaveBeenCalled()
    // MapLibre says it has not loaded while a source is updating, as right
    // after Back: the map must move all the same.
    mapState.loaded = false
    act(() => mapState.onLoad())
    rerender(<HexMap overview={OVERVIEW} species={null} selectedCell={alsoOnScreen} />)
    expect(mapState.easeTo).not.toHaveBeenCalled()
    rerender(<HexMap overview={OVERVIEW} species={null} selectedCell={offScreen} />)
    expect(mapState.easeTo).toHaveBeenCalledTimes(1)
    const [lat, lng] = cellToLatLng(offScreen)
    expect(mapState.easeTo.mock.calls[0][0]).toEqual({ center: [lng, lat], duration: 900 })
    // A hexagon of another size: the map zooms to hexagons of its size.
    rerender(<HexMap overview={OVERVIEW} species={null} selectedCell={finer} />)
    expect(mapState.easeTo).toHaveBeenCalledTimes(2)
    expect(mapState.easeTo.mock.calls[1][0].zoom).toBe(zoomShowingResolution(shown + 2))
    // Its outline is the one the address names.
    expect(mapState.layers.at(-1).id).toBe(`selected-cell-${finer}`)
  })

  it('stays on the hexagon instead of moving to the species while the panel is open, and does not jump on closing', async () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={ROBIN} selectedCell="8428c9bffffffff" />)
    await settle()
    expect(mapState.fitBounds).not.toHaveBeenCalled()
    rerender(<HexMap overview={OVERVIEW} species={ROBIN} selectedCell={null} />)
    await settle()
    expect(mapState.fitBounds).not.toHaveBeenCalled()
  })
})

describe('a rank map', () => {
  // The genus Turdus, as App gives it (with the id that changes each time
  // it is opened).
  const TURDUS = { rank: 'genus', name: 'Turdus', id: 1, path: [
    { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
    { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
  ] }
  const TURDUS_ID = 'Animalia|Chordata|Aves|Passeriformes|Turdidae|Turdus|genus'
  const TURDUS_PATH = 'kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes&family=Turdidae&genus=Turdus'

  // A pretend API answer for the rank's map, in the overview's shape.
  function rankAnswer(res, yearsCounted = null) {
    return {
      rank: 'genus', name: 'Turdus', res, years_counted: yearsCounted, hexagon_count: 2,
      records_shown: 965537, records_no_year_left_out: yearsCounted ? 18 : 0,
      cells: ['8428c9bffffffff', '842800bffffffff'], occurrences: [965000, 537], distinct_species: [3, 1],
    }
  }

  function renderRank(props = {}) {
    return render(<HexMap overview={OVERVIEW} species={null} rank={TURDUS} rankId={TURDUS_ID} {...props} />)
  }

  it('shows only the rank\'s species, with the overview\'s toggle, colours and bins, and names the rank', async () => {
    const urls = stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1])) }))
    renderRank()
    await settle()
    expect(urls()).toEqual([`/api/ranks/genus/hexes?res=4&${TURDUS_PATH}`])
    expect(visibleLayers()).toEqual([`rank-${TURDUS_ID}-all-r4`])
    const layer = () => mapState.layers.find((l) => l.id === `rank-${TURDUS_ID}-all-r4`)
    expect(layer().props.updateTriggers.getFillColor).toEqual(['species', null])
    const caption = document.querySelector('.legend-caption')
    expect(caption.textContent).toBe('Species of Turdus per hexagon')
    expect(caption.querySelector('i').textContent).toBe('Turdus')
    expect([...document.querySelectorAll('.legend-edge')].map((el) => el.textContent))
      .toEqual(['1', '3', '10', '30', '100', '300', '1K', '3K+'])
    expect(document.querySelector('.legend-summary').textContent).toBe('965,537 records in 2 hexagons')

    act(() => screen.getByRole('button', { name: 'Records' }).click())
    expect(layer().props.updateTriggers.getFillColor).toEqual(['records', null])
    expect(document.querySelector('.legend-caption').textContent).toBe('Records of Turdus per hexagon')
    expect([...document.querySelectorAll('.legend-edge')].map((el) => el.textContent))
      .toEqual(['1', '10', '100', '1K', '10K', '100K', '1M+'])
    // One answer colours both views.
    expect(urls()).toHaveLength(1)
  })

  it('says which rank is shown, with an x back to all species', async () => {
    stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1])) }))
    const onCloseRank = vi.fn()
    renderRank({ onCloseRank })
    await settle()
    const label = document.querySelector('.rank-showing')
    expect(label.textContent).toBe('Showing genus Turdus×')
    expect(label.querySelector('i').textContent).toBe('Turdus')
    act(() => screen.getByRole('button', { name: 'Stop showing this rank and show all species' }).click())
    expect(onCloseRank).toHaveBeenCalledTimes(1)
  })

  it('writes a family in plain type', async () => {
    stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1])) }))
    const family = { rank: 'family', name: 'Turdidae', id: 1, path: TURDUS.path.slice(0, 5) }
    render(<HexMap overview={OVERVIEW} species={null} rank={family} rankId="Turdidae|family" />)
    await settle()
    expect(document.querySelector('.rank-showing').textContent).toBe('Showing family Turdidae×')
    expect(document.querySelector('.legend-caption').textContent).toBe('Species of Turdidae per hexagon')
    expect(document.querySelector('.rank-showing i')).toBeNull()
  })

  it('maps the rank for a year range, with the no-date note', async () => {
    const urls = stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1]), [2000, 2020]) }))
    renderRank({ years: { from: 2000, to: 2020 } })
    await settle()
    expect(urls()).toEqual([`/api/ranks/genus/hexes?res=4&year_from=2000&year_to=2020&${TURDUS_PATH}`])
    expect(visibleLayers()).toEqual([`rank-${TURDUS_ID}-2000-2020-r4`])
    expect(document.querySelector('.legend-summary').textContent).toBe('965,537 records in 2 hexagons, 2000 to 2020')
    expect(document.querySelector('.legend-left-out').textContent).toBe('18 records with no date are not shown.')
  })

  it('moves the map to the rank once per opening, but not while a hexagon is open', async () => {
    stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1])) }))
    const { rerender } = renderRank()
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
    expect(mapState.fitBounds.mock.calls[0][1]).toMatchObject({ maxZoom: 8 })
    rerender(<HexMap overview={OVERVIEW} species={null} rank={{ ...TURDUS }} rankId={TURDUS_ID} />)
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
    rerender(<HexMap overview={OVERVIEW} species={null} rank={{ ...TURDUS, id: 2 }} rankId={TURDUS_ID}
      selectedCell="8428c9bffffffff" />)
    await settle()
    expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
  })

  it('goes back to the overview when the rank closes', async () => {
    stubHexes((url) => ({ body: rankAnswer(Number(url.match(/res=(\d)/)[1])) }))
    const { rerender } = renderRank()
    await settle()
    rerender(<HexMap overview={OVERVIEW} species={null} rank={null} rankId={null} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r4'])
    expect(document.querySelector('.rank-showing')).toBeNull()
    expect(screen.getByText('Distinct species per hexagon')).toBeTruthy()
  })

  it('says clearly when the rank\'s map cannot be loaded', async () => {
    stubHexes(() => ({ status: 500, body: { detail: 'boom' } }))
    renderRank()
    await settle()
    expect(document.querySelector('.legend-status.error').textContent).toMatch(/the map of this genus/)
    expect(document.querySelector('.status-message')).toBeNull()
  })
})

describe('the zoom for a resolution', () => {
  it('is inside that resolution\'s tier', () => {
    for (const res of [4, 5, 6, 7]) {
      expect(pickResolution(zoomShowingResolution(res), null)).toBe(res)
    }
  })
})

describe('an area of interest', () => {
  // A mouse event on the map at [lng, lat], as react-map-gl gives it.
  function at(lng, lat) {
    return { lngLat: { lng, lat }, preventDefault: () => {} }
  }

  // Opens the "Create an area of interest" panel.
  function openPanel() {
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
  }

  // Presses the panel's "Box" button (starting or cancelling a box).
  function pressBox() {
    fireEvent.click(screen.getByRole('button', { name: 'Box' }))
  }

  // The lines the map draws for the area: its shapes, then a box being
  // dragged, each as its list of [lng, lat].
  function outlined() {
    return mapState.sources['area-box']?.features.map((feature) => feature.geometry.coordinates) ?? []
  }

  it('offers "Create an area of interest" on the overview and a rank map, greyed out with a reason on a species map', () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    expect(screen.getByRole('button', { name: 'Create an area of interest' }).disabled).toBe(false)
    rerender(<HexMap overview={OVERVIEW} species={ROBIN} />)
    const button = screen.getByRole('button', { name: 'Create an area of interest' })
    expect(button.disabled).toBe(true)
    expect(button.closest('[title]').getAttribute('title')).toBe(DRAW_UNAVAILABLE)
    expect(document.getElementById(button.getAttribute('aria-describedby')).textContent).toBe(DRAW_UNAVAILABLE)
  })

  it('opens and closes its panel, listing no shapes yet', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    expect(screen.queryByRole('region', { name: 'Area of interest' })).toBeNull()
    openPanel()
    const panel = screen.getByRole('region', { name: 'Area of interest' })
    expect(panel.querySelector('strong').textContent).toBe('No area yet')
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Draw', 'Paste'])
    expect(screen.getByRole('button', { name: 'Previous area' }).disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Clear area' }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Area of interest' })).toBeNull()
    // The main button opens and closes it too.
    openPanel()
    openPanel()
    expect(screen.queryByRole('region', { name: 'Area of interest' })).toBeNull()
  })

  it('stops the map panning, shows the hint and the crosshair, and hands on the box and the resolution', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    expect(mapState.props.dragPan).toBe(true)
    openPanel()
    pressBox()
    expect(mapState.props.dragPan).toBe(false)
    expect(mapState.props.touchZoomRotate).toBe(false)
    expect(mapState.props.boxZoom).toBe(false)
    expect(mapState.props.cursor).toBe('crosshair')
    expect(screen.getByText(DRAW_HINT)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Box' }).getAttribute('aria-pressed')).toBe('true')
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    act(() => mapState.props.onMouseMove(at(-120.4, 50.6)))
    // The box shows while it is dragged.
    expect(outlined()).toEqual([[[-120.4, 50.6], [-120.1, 50.6], [-120.1, 50.8], [-120.4, 50.8], [-120.4, 50.6]]])
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    const resolution = pickResolution(4.5, null)
    expect(onAreaDrawn).toHaveBeenCalledWith({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, resolution)
    expect(mapState.props.dragPan).toBe(true)
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
    // The panel stays open, for the next shape.
    expect(screen.getByRole('region', { name: 'Area of interest' })).toBeTruthy()
  })

  it('draws with one finger too', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    openPanel()
    pressBox()
    act(() => mapState.props.onTouchStart({ ...at(-120.6, 50.55), points: [{}] }))
    act(() => mapState.props.onTouchMove({ ...at(-120.1, 50.8), points: [{}] }))
    act(() => mapState.props.onTouchEnd({ points: [] }))
    expect(onAreaDrawn).toHaveBeenCalledWith({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, expect.any(Number))
  })

  it('cancels with Esc, the Box button again, or closing the panel, drawing nothing', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    openPanel()
    pressBox()
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
    expect(mapState.props.dragPan).toBe(true)
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    pressBox()
    pressBox()
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
    pressBox()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(mapState.props.dragPan).toBe(true)
    expect(onAreaDrawn).not.toHaveBeenCalled()
  })

  it('ends drawing when the map turns to another page (Back, a link, a panel), and not otherwise', () => {
    const props = { overview: OVERVIEW, species: null }
    const { rerender } = render(<HexMap {...props} />)
    openPanel()
    // Starts drawing with the Box button and checks it is on.
    function startDrawing() {
      pressBox()
      expect(screen.getByText(DRAW_HINT)).toBeTruthy()
      expect(mapState.props.dragPan).toBe(false)
    }
    // Checks drawing is off: the hint and the map's dragging.
    function expectNotDrawing() {
      expect(screen.queryByText(DRAW_HINT)).toBeNull()
      expect(mapState.props.dragPan).toBe(true)
    }
    startDrawing()
    // The same page again (for example a list's filter): still drawing.
    rerender(<HexMap {...props} />)
    expect(screen.getByText(DRAW_HINT)).toBeTruthy()
    // A hexagon's panel (as after Back to it).
    rerender(<HexMap {...props} selectedCell="8628c9b47ffffff" />)
    expectNotDrawing()
    // Another area.
    startDrawing()
    rerender(<HexMap {...props} selectedCell="8628c9b47ffffff" selectedArea={AREA} />)
    expectNotDrawing()
    // No hexagon and no area.
    startDrawing()
    rerender(<HexMap {...props} />)
    expectNotDrawing()
    // A rank.
    startDrawing()
    rerender(<HexMap {...props} rank={{ rank: 'genus', name: 'Turdus', path: [], id: 1 }} rankId="genus-turdus" />)
    expectNotDrawing()
  })

  it('does not open a hexagon while drawing, nor when the mouse is let go', () => {
    const onCellClick = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onCellClick={onCellClick} />)
    openPanel()
    pressBox()
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    expect(onCellClick).not.toHaveBeenCalled()
  })

  // An area of one box holding the overview's resolution 6 hexagon
  // (8628c9b47ffffff) and none of the overview's other hexagons; and the
  // same with a second box.
  const AREA_BOX = { west: -130.4, south: 48.8, east: -130.25, north: 48.9 }
  const AREA = { shapes: [boxShapeOf(AREA_BOX)], res: 6, cells: ['8628c9b47ffffff'] }
  const TWO = { ...AREA, shapes: [boxShapeOf(AREA_BOX), boxShapeOf({ west: -120.6, south: 50.55, east: -120.1,
    north: 50.8 })] }

  // The fill colour a layer gives one of its rows.
  function fillOf(layerId, row) {
    return mapState.layers.find((layer) => layer.id === layerId).props.getFillColor(row)
  }

  it('fades every hexagon outside a selected area, at any resolution, keeping the selected ones as they are', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    const usual = Object.fromEntries([4, 5, 6, 7].map((r) => [r, fillOf(overviewLayerId(r), OVERVIEW.rows[r][0])]))
    cleanup()
    const elsewhere = { h3Cell: '862800b37ffffff', occurrences: 10, distinctSpecies: 5 }
    render(<HexMap overview={{ ...OVERVIEW, rows: { ...OVERVIEW.rows, 6: [...OVERVIEW.rows[6], elsewhere] } }}
      species={null} selectedArea={AREA} />)
    // The selected hexagon and the resolution 7 one inside it keep their
    // colour; the resolution 4 and 5 ones that hold it are faded, as only a
    // small part of each is in the area. (The map shows only the area's own
    // size while it is there, see the tests below.)
    for (const r of [6, 7]) {
      expect(fillOf(overviewLayerId(r), OVERVIEW.rows[r][0])).toEqual(usual[r])
    }
    for (const r of [4, 5]) {
      expect(fillOf(overviewLayerId(r), OVERVIEW.rows[r][0])[3]).toBe(Math.round(usual[r][3] * FADED_SHARE))
    }
    // A hexagon outside the area keeps its colour at 30% of its opacity.
    const faded = fillOf(overviewLayerId(6), elsewhere)
    expect(faded.slice(0, 3)).toEqual(usual[6].slice(0, 3))
    expect(faded[3]).toBe(Math.round(usual[6][3] * FADED_SHARE))
    expect(FADED_SHARE).toBe(0.3)
    // deck.gl is told the colours changed.
    expect(mapState.layers.find((layer) => layer.id === overviewLayerId(6)).props.updateTriggers.getFillColor)
      .toEqual(['species', '6:b:-130.4,48.8,-130.25,48.9'])
  })

  it('outlines each shape, a dark solid line on a white edge, with no outline around the group or per hexagon', () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={TWO} />)
    expect(mapState.sources['area-group']).toBeUndefined()
    expect(mapState.mapLayers['area-box-edge'].paint)
      .toEqual({ 'line-color': '#ffffff', 'line-width': SHAPE_OUTLINE.lightWidth })
    expect(mapState.mapLayers['area-box-line'].paint)
      .toEqual({ 'line-color': '#1a1a1a', 'line-width': SHAPE_OUTLINE.darkWidth })
    expect(SHAPE_OUTLINE.lightWidth).toBeGreaterThan(SHAPE_OUTLINE.darkWidth)
    expect(outlined()).toEqual([
      [[-130.4, 48.8], [-130.25, 48.8], [-130.25, 48.9], [-130.4, 48.9], [-130.4, 48.8]],
      [[-120.6, 50.55], [-120.1, 50.55], [-120.1, 50.8], [-120.6, 50.8], [-120.6, 50.55]],
    ])
    expect(mapState.layers.some((layer) => layer.id.startsWith('selected-area'))).toBe(false)
    expect(mapState.layers.every((layer) => layer.props.stroked === false || layer.props.filled !== false)).toBe(true)
  })

  it('draws a new box anywhere while an area is selected, showing it beside the area\'s shapes', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} onAreaDrawn={onAreaDrawn} />)
    openPanel()
    // Draws one box from corner to corner and returns what was handed on.
    function draw(from, to) {
      pressBox()
      act(() => mapState.props.onMouseDown(at(...from)))
      act(() => mapState.props.onMouseMove(at(...to)))
      // The new box shows while it is dragged, after the area's own box.
      expect(outlined()).toHaveLength(2)
      expect(outlined()[0][0]).toEqual([AREA_BOX.west, AREA_BOX.south])
      expect(outlined()[1][2]).toEqual(to)
      act(() => mapState.props.onMouseUp(at(...to)))
      expect(mapState.props.dragPan).toBe(true)
      return onAreaDrawn.mock.calls.at(-1)[0]
    }
    // Fully outside the area (around Kamloops, far from it).
    expect(draw([-120.6, 50.55], [-120.1, 50.8])).toEqual({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 })
    // Over part of it.
    expect(draw([-130.3, 48.85], [-130.0, 49.1])).toEqual({ west: -130.3, south: 48.85, east: -130, north: 49.1 })
    // Too small: still handed on, for App to keep the area and say why.
    expect(draw([-125, 52], [-124.999, 52.001])).toEqual({ west: -125, south: 52, east: -124.999, north: 52.001 })
    expect(onAreaDrawn).toHaveBeenCalledTimes(3)
    // Once drawing ends, only the area's own box is outlined.
    expect(outlined()).toHaveLength(1)
  })

  it('fits the shapes in view when the page opens on an area', () => {
    // A box around Kamloops and a polygon south of it, at resolution 6.
    const shapes = readAreaText('b:-120.6,50.55,-120.1,50.8;p:-121,50,-119.2,50,-119,51,-120.4,51.2')
    const area = { shapes, res: 6, cells: boxCells({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, 6) }
    mapState.fitBounds = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={area} />)
    act(() => mapState.onLoad())
    const [[bounds, options]] = mapState.fitBounds.mock.calls
    expect(bounds).toEqual([[-121, 50], [-119, 51.2]])
    expect(bounds).toEqual(shapesBounds(shapes))
    // The test page cannot measure the panels, so the usual margins.
    expect(options).toEqual({ padding: fitPadding(1280, 800), duration: 0 })
  })

  describe('fitting the map to a paste', () => {
    // A polygon around Kamloops and one around Kelowna, about 90 km apart,
    // as one paste gives them, and the box around both.
    const PASTED = readAreaText('p:-120.6,50.55,-120.1,50.55,-120.1,50.8,-120.6,50.8;'
      + 'p:-119.6,49.8,-119.3,49.8,-119.3,50,-119.6,50')
    const BOTH = [[-120.6, 49.8], [-119.3, 50.8]]
    const PASTED_AREA = { shapes: PASTED, res: 6, cells: [] }
    // Another area, as Back or Forward, or a box drawn, would give.
    const OTHER_AREA = { ...AREA, cells: [] }

    it('moves the map so every pasted shape is in view, however far apart, once per paste', () => {
      const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedArea={OTHER_AREA} />)
      act(() => mapState.onLoad())
      mapState.fitBounds = vi.fn()
      const fit = { shapes: PASTED, id: 1 }
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={PASTED_AREA} fitArea={fit} />)
      expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
      const [bounds, options] = mapState.fitBounds.mock.calls[0]
      expect(bounds).toEqual(BOTH)
      expect(options).toEqual({ padding: fitPadding(1280, 800), duration: 900 })
      // Back to the area before (the same paste still the last): no move.
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={OTHER_AREA} fitArea={fit} />)
      // Forward again, or a box drawn: no move either.
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={PASTED_AREA} fitArea={fit} />)
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} fitArea={fit} />)
      expect(mapState.fitBounds).toHaveBeenCalledTimes(1)
      // A new paste, even of the same shapes, moves it again.
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={PASTED_AREA} fitArea={{ shapes: PASTED, id: 2 }} />)
      expect(mapState.fitBounds).toHaveBeenCalledTimes(2)
    })

    it('does not move the map for a box drawn, with no paste', () => {
      const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
      act(() => mapState.onLoad())
      mapState.fitBounds = vi.fn()
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={OTHER_AREA} />)
      rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={PASTED_AREA} />)
      expect(mapState.fitBounds).not.toHaveBeenCalled()
    })

    it('leaves room for the panels on the left and the right, unless the window is too narrow', () => {
      // A wide window, the "Create an area of interest" panel reaching 344
      // pixels in from the left, the area's panel 404 from the right.
      expect(areaFitPadding(1280, 800, 344, 404)).toEqual({ top: 24, left: 368, right: 428, bottom: 150 })
      // Nothing measured: the usual margins.
      expect(areaFitPadding(1280, 800, 0, 0)).toEqual(fitPadding(1280, 800))
      // A phone: no room left between the panels, so the usual margins.
      expect(areaFitPadding(390, 700, 344, 378)).toEqual(fitPadding(390, 700))
      expect(AREA_FIT_MIN_MAP_PX).toBe(200)
    })

    it('measures how far the panels reach over the map', () => {
      // A page 1280 pixels wide with the map filling it.
      const page = document.createElement('div')
      page.className = 'map-area'
      const container = document.createElement('div')
      const left = document.createElement('div')
      left.className = 'top-left-controls'
      const right = document.createElement('div')
      right.className = 'top-right-controls'
      page.append(container, left, right)
      document.body.append(page)
      const rect = (from, to) => () => ({ left: from, right: to, width: to - from })
      container.getBoundingClientRect = rect(0, 1280)
      left.getBoundingClientRect = rect(12, 332)
      right.getBoundingClientRect = rect(888, 1268)
      expect(panelsCover(container)).toEqual({ left: 332, right: 392 })
      // Nothing on screen at the right (no width): nothing covered there.
      right.getBoundingClientRect = rect(0, 0)
      expect(panelsCover(container)).toEqual({ left: 332, right: 0 })
      page.remove()
      expect(panelsCover({ clientWidth: 1280 })).toEqual({ left: 0, right: 0 })
    })
  })

  it('fades nothing with no area selected', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    expect(mapState.layers.find((layer) => layer.id === overviewLayerId(6)).props.updateTriggers.getFillColor)
      .toEqual(['species', null])
    expect(mapState.sources['area-box']).toBeUndefined()
    expect(mapState.layers.some((layer) => layer.id.startsWith('area-hexagons-'))).toBe(false)
  })

  it("lists the area's shapes, each with an x, and hands on x, Back and Clear area", () => {
    const onRemoveShape = vi.fn()
    const onAreaBack = vi.fn()
    const onClearArea = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={TWO} onRemoveShape={onRemoveShape}
      onAreaBack={onAreaBack} onClearArea={onClearArea} />)
    openPanel()
    const panel = screen.getByRole('region', { name: 'Area of interest' })
    expect(panel.querySelector('strong').textContent).toBe('Your area: 2 shapes')
    expect([...panel.querySelectorAll('.aoi-shapes li span')].map((item) => item.textContent))
      .toEqual(['Shape 1: box', 'Shape 2: box'])
    fireEvent.click(screen.getByRole('button', { name: 'Remove shape 2' }))
    expect(onRemoveShape).toHaveBeenCalledWith(1)
    fireEvent.click(screen.getByRole('button', { name: 'Previous area' }))
    expect(onAreaBack).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Clear area' }))
    expect(onClearArea).toHaveBeenCalledTimes(1)
  })

  it('offers Show area while the area panel is closed, beside the main button, not while drawing', () => {
    const onShow = vi.fn()
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
    rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} onShowArea={onShow} />)
    const buttons = [...document.querySelectorAll('.draw-box-buttons button')].map((button) => button.textContent)
    expect(buttons).toEqual(['Create an area of interest', 'Show area'])
    fireEvent.click(screen.getByRole('button', { name: 'Show area' }))
    expect(onShow).toHaveBeenCalledTimes(1)
    openPanel()
    pressBox()
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
  })

  it('shows nothing of the area on a single species map', () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} onClearArea={vi.fn()} onShowArea={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Area of interest' })).toBeNull()
  })

  it('hands a paste on (the map\'s zoom plays no part), and shows why it was refused', () => {
    const onAreaPasted = vi.fn(() => 'The polygon has a hole; holes are not supported yet.')
    render(<HexMap overview={OVERVIEW} species={null} onAreaPasted={onAreaPasted} />)
    openPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Paste' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'POLYGON((0 0, 1 0, 1 1, 0 0))' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this shape' }))
    expect(onAreaPasted).toHaveBeenCalledWith('POLYGON((0 0, 1 0, 1 1, 0 0))')
    expect(screen.getByRole('alert').textContent).toBe('The polygon has a hole; holes are not supported yet.')
  })

  it('offers the hexagon size, Auto showing its choice, and hands a change on', () => {
    const onAreaSizeChange = vi.fn()
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, res: 7, picked: false }}
      onAreaSizeChange={onAreaSizeChange} />)
    openPanel()
    const choice = screen.getByRole('combobox', { name: 'Hexagon size' })
    expect([...choice.options].map((option) => option.textContent))
      .toEqual(['Auto (about 3 km)', 'About 52 km', 'About 20 km', 'About 7 km', 'About 3 km'])
    expect(choice.value).toBe('auto')
    fireEvent.change(choice, { target: { value: '5' } })
    expect(onAreaSizeChange).toHaveBeenCalledWith(5)
    rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, res: 5, picked: true }}
      onAreaSizeChange={onAreaSizeChange} />)
    expect(screen.getByRole('combobox', { name: 'Hexagon size' }).value).toBe('5')
    expect(screen.getByRole('option', { name: 'Auto' })).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox', { name: 'Hexagon size' }), { target: { value: 'auto' } })
    expect(onAreaSizeChange).toHaveBeenLastCalledWith(null)
  })

  it('outlines a pasted polygon like a box', () => {
    const polygon = readAreaText('p:-121,50,-119.2,50,-119,51,-120.4,51.2')[0]
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, shapes: [polygon] }} />)
    expect(outlined()).toEqual([[[-121, 50], [-119.2, 50], [-119, 51], [-120.4, 51.2], [-121, 50]]])
  })

  it('shows the message about a shape refused in the panel, which its x dismisses', () => {
    const onDismiss = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} areaMessage="The box is about 9.9 km², too small."
      onDismissAreaMessage={onDismiss} />)
    openPanel()
    expect(screen.getByRole('alert').textContent).toContain('The box is about 9.9 km², too small.')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss this message' }))
    expect(onDismiss).toHaveBeenCalled()
  })
})

describe("the map locked to the area's size", () => {
  // An area of one box at resolution 6 (about 7 km) holding the overview's
  // resolution 6 hexagon (8628c9b47ffffff) and an empty one beside it. The
  // map opens on resolution 4 hexagons.
  const BOX = { west: -130.4, south: 48.8, east: -130.25, north: 48.9 }
  const AREA = { shapes: [boxShapeOf(BOX)], res: 6, cells: ['8628c9b47ffffff', '8628c9b4fffffff'] }
  const INSIDE = OVERVIEW.rows[6][0]
  const YEARS = { from: 2000, to: 2020 }
  const LOCKED_NOTE = 'While an area is shown, the whole map uses its hexagon size, about 7 km, at every zoom. '
    + 'Clear the area to go back.'

  // The hexagon size label at the top left, and the legend's area line.
  function sizeLabel() {
    return document.querySelector('.hex-size-label').textContent
  }
  function legendNote() {
    return document.querySelector('.legend-area')?.textContent ?? null
  }

  // Moves the map to the middle of the zoom tier showing this resolution.
  function zoomTo(res) {
    act(() => mapState.onMove({ viewState: { zoom: zoomShowingResolution(res) } }))
  }

  // A pretend map answer from the API (overview or rank) at res.
  function mapAnswer(res) {
    const cells = res === 6 ? ['8628c9b47ffffff', '862800b37ffffff'] : [OVERVIEW.rows[res][0].h3Cell]
    return {
      res, year_from: 2000, year_to: 2020, years_counted: [2000, 2020], hexagon_count: cells.length,
      records_shown: 99, records_no_year_left_out: 0, cells,
      occurrences: cells.map(() => 40), distinct_species: cells.map(() => 30),
    }
  }

  it("shows the area's size on the whole map at every zoom, the area bright and the rest faded", () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} />)
    for (const res of [4, 5, 6, 7, 4]) {
      zoomTo(res)
      expect(visibleLayers()).toEqual(['bc-hexagons-r6'])
    }
    // Only the map's own layers: no layer of the area's own, and the
    // hexagons with no records are not drawn (the overview file has none).
    expect(mapState.layers.map((layer) => layer.id)).toEqual([4, 5, 6, 7].map(overviewLayerId))
    const layer = mapState.layers.find((l) => l.id === overviewLayerId(6))
    expect(layer.props.data).toBe(OVERVIEW.rows[6])
    expect(layer.props.getFillColor(INSIDE)).toEqual(hexagonColour(INSIDE, COLOUR_SCHEMES.species))
    const elsewhere = { h3Cell: '862800b37ffffff', occurrences: 10, distinctSpecies: 5 }
    expect(layer.props.getFillColor(elsewhere)[3])
      .toBe(Math.round(hexagonColour(elsewhere, COLOUR_SCHEMES.species)[3] * FADED_SHARE))
  })

  it('says so in the label and the legend, and not without an area', () => {
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} />)
    expect(sizeLabel()).toBe("Hexagons ~7 km (the area's size)")
    expect(legendNote()).toBe(LOCKED_NOTE)
    rerender(<HexMap overview={OVERVIEW} species={null} />)
    expect(sizeLabel()).toBe('Hexagons ~52 km')
    expect(legendNote()).toBeNull()
  })

  it('follows Auto choosing another size and a size picked, and goes back to the zoom after Clear area', () => {
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} />)
    zoomTo(5)
    expect(visibleLayers()).toEqual(['bc-hexagons-r6'])
    // Auto moves to about 3 km (a small shape added).
    rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, res: 7, picked: false }} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r7'])
    expect(sizeLabel()).toBe("Hexagons ~3 km (the area's size)")
    expect(legendNote()).toContain('about 3 km')
    // About 20 km picked by hand.
    rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, res: 5, picked: true }} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r5'])
    expect(sizeLabel()).toBe("Hexagons ~20 km (the area's size)")
    // Cleared: the size follows the zoom again, from where the map is now.
    zoomTo(7)
    rerender(<HexMap overview={OVERVIEW} species={null} />)
    expect(visibleLayers()).toEqual(['bc-hexagons-r7'])
    expect(sizeLabel()).toBe('Hexagons ~3 km')
    zoomTo(4)
    expect(visibleLayers()).toEqual(['bc-hexagons-r4'])
  })

  it("draws white edges for the locked size's hexagons on screen, none below about 20 pixels", () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, res: 7 }} />)
    const r7 = () => mapState.layers.find((l) => l.id === overviewLayerId(7)).props
    // At the opening zoom, hexagons of about 3 km are about 2 pixels wide.
    expect(hexagonWidthPixels(7, 4.5)).toBeLessThan(20)
    expect(r7().stroked).toBe(false)
    const zoom = zoomShowingResolution(7)
    act(() => mapState.onMove({ viewState: { zoom } }))
    expect(hexagonWidthPixels(7, zoom)).toBeGreaterThan(24)
    expect(r7()).toMatchObject({ stroked: true, getLineColor: [255, 255, 255, 255] })
    // Zoomed out to where they are about 21 pixels wide: a faint line, and
    // under 20 none, though the zoom's own size would be larger there.
    act(() => mapState.onMove({ viewState: { zoom: 8.02 } }))
    expect(outlineOpacity(hexagonWidthPixels(7, 8.02))).toBe(r7().getLineColor[3] / 255)
    act(() => mapState.onMove({ viewState: { zoom: 7.5 } }))
    expect(r7().stroked).toBe(false)
  })

  it('shows its own numbers on hover, and opens a hexagon on a click without changing the zoom', () => {
    const onCellClick = vi.fn()
    const props = { overview: OVERVIEW, species: null, selectedArea: AREA, onCellClick }
    const { rerender } = render(<HexMap {...props} />)
    act(() => mapState.onLoad())
    const layer = mapState.layers.find((l) => l.id === overviewLayerId(6))
    const tooltip = mapState.getTooltip({ layer, object: INSIDE })
    expect(tooltip.html).toContain('<strong>10</strong> records')
    act(() => mapState.onClick({ object: INSIDE }))
    expect(onCellClick).toHaveBeenCalledWith(INSIDE.h3Cell)
    rerender(<HexMap {...props} selectedCell={INSIDE.h3Cell} />)
    expect(mapState.easeTo).not.toHaveBeenCalled()
    expect(mapState.layers.at(-1).id).toBe(`selected-cell-${INSIDE.h3Cell}`)
    // A hexagon of another size (as after the browser's Back) off screen:
    // the map moves to it without zooming, as zooming would not change the size.
    mapState.bounds = [[-121, 50], [-120, 51]]
    rerender(<HexMap {...props} selectedCell={OVERVIEW.rows[5][0].h3Cell} />)
    expect(mapState.easeTo).toHaveBeenCalledTimes(1)
    const [lat, lng] = cellToLatLng(OVERVIEW.rows[5][0].h3Cell)
    expect(mapState.easeTo.mock.calls[0][0]).toEqual({ center: [lng, lat], duration: 900 })
  })

  it("asks the API at the area's size for a year range, and for a rank", async () => {
    const urls = stubHexes((url) => ({ body: { ...mapAnswer(Number(url.match(/res=(\d)/)[1])), rank: 'genus' } }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} years={YEARS} />)
    await settle()
    zoomTo(4)
    zoomTo(7)
    await settle()
    expect(urls()).toEqual(['/api/overview/hexes?res=6&year_from=2000&year_to=2020'])
    expect(visibleLayers()).toEqual(['overview-2000-2020-r6'])
    const rank = { rank: 'genus', name: 'Turdus', id: 1, path: [{ rank: 'genus', name: 'Turdus' }] }
    rerender(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} rank={rank} rankId="Turdus|genus" />)
    await settle()
    expect(urls().at(-1)).toBe('/api/ranks/genus/hexes?res=6&genus=Turdus')
    expect(visibleLayers()).toHaveLength(1)
    expect(visibleLayers()[0]).toMatch(/^rank-Turdus\|genus-.*-r6$/)
  })

  it("leaves a single species' map to follow the zoom", async () => {
    const urls = stubHexes((url) => ({ body: hexes('59PMB', Number(url.match(/res=(\d)/)[1]), ['8428c9bffffffff'], [3]) }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} selectedArea={AREA} />)
    await settle()
    expect(urls()).toEqual(['/api/species/59PMB/hexes?res=4'])
    expect(sizeLabel()).toBe('Hexagons ~52 km')
    expect(legendNote()).toBeNull()
  })
})

describe('drawing a polygon', () => {
  // Three points of a triangle near Kamloops, and one that would make an
  // edge cross the first.
  const A = [-120.6, 50.5]
  const B = [-120.0, 50.5]
  const C = [-120.3, 50.9]
  const AREA = { shapes: [boxShapeOf({ west: -125, south: 54, east: -124, north: 54.5 })], res: 6, cells: [] }

  // Opens the panel and starts a polygon.
  function startPolygon() {
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
    fireEvent.click(screen.getByRole('button', { name: 'Polygon' }))
  }

  // A click on the map at [lng, lat], as react-map-gl gives it.
  function clickAt([lng, lat]) {
    act(() => mapState.props.onClick({ lngLat: { lng, lat }, point: { x: lng * 100, y: -lat * 100 } }))
  }

  // The points of the polygon being drawn, from what the map draws.
  function drawnPoints() {
    return (mapState.sources['area-polygon']?.features ?? [])
      .filter((feature) => feature.properties.kind === 'point').map((feature) => feature.geometry.coordinates)
  }

  // Presses a key on the page.
  function press(key) {
    fireEvent.keyDown(window, { key })
  }

  it('offers Polygon beside Box, with its hint, still panning by drag and not zooming on a double-click', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
    expect(screen.getByText(DRAW_HELP)).toBeTruthy()
    const polygonButton = screen.getByRole('button', { name: 'Polygon' })
    expect(polygonButton.previousElementSibling.textContent).toBe('Box')
    fireEvent.click(polygonButton)
    expect(screen.getByRole('button', { name: 'Polygon' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('status').textContent).toBe(POLYGON_HINT)
    expect(POLYGON_HINT).toBe('Click to add points. Click the first point, press Enter or double-click to finish. '
      + 'Backspace removes the last point, Esc cancels.')
    expect(mapState.props).toMatchObject({ dragPan: true, doubleClickZoom: false, cursor: 'crosshair' })
    expect(['Finish', 'Undo point', 'Cancel'].every((name) => screen.getByRole('button', { name }))).toBe(true)
  })

  it('shows the points, the edges and the line to the mouse, and closes on the first point', () => {
    const onAreaPolygon = vi.fn(() => null)
    const onCellClick = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaPolygon={onAreaPolygon} onCellClick={onCellClick} />)
    startPolygon()
    clickAt(A)
    clickAt(B)
    act(() => mapState.props.onMouseMove({ lngLat: { lng: -120.2, lat: 50.8 } }))
    expect(drawnPoints()).toEqual([A, B])
    const kinds = mapState.sources['area-polygon'].features.map((feature) => feature.properties.kind)
    expect(kinds).toEqual(['edges', 'next', 'point', 'point'])
    expect(Object.keys(mapState.mapLayers).filter((id) => id.startsWith('area-polygon')))
      .toEqual(['area-polygon-edge', 'area-polygon-line', 'area-polygon-next', 'area-polygon-points'])
    // A hexagon under a click does not open while drawing.
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    expect(onCellClick).not.toHaveBeenCalled()
    clickAt(C)
    clickAt([-120.57, 50.52])
    expect(onAreaPolygon).toHaveBeenCalledTimes(1)
    expect(onAreaPolygon).toHaveBeenCalledWith([A, B, C])
    expect(mapState.sources['area-polygon']).toBeUndefined()
    expect(mapState.props.doubleClickZoom).toBe(true)
    // The click that closed it does not open a hexagon either.
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    expect(onCellClick).not.toHaveBeenCalled()
  })

  it('finishes with a double-click, Enter and the Finish button', () => {
    const onAreaPolygon = vi.fn(() => null)
    render(<HexMap overview={OVERVIEW} species={null} onAreaPolygon={onAreaPolygon} />)
    for (const finish of [
      () => act(() => mapState.props.onDblClick()),
      () => press('Enter'),
      () => fireEvent.click(screen.getByRole('button', { name: 'Finish' })),
    ]) {
      if (!screen.queryByRole('region', { name: 'Area of interest' })) {
        fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
      }
      fireEvent.click(screen.getByRole('button', { name: 'Polygon' }))
      clickAt(A)
      clickAt(B)
      clickAt(C)
      // A double-click's two clicks land on the last point: no point added.
      clickAt(C)
      finish()
      expect(onAreaPolygon).toHaveBeenLastCalledWith([A, B, C])
      expect(screen.getByRole('button', { name: 'Polygon' }).getAttribute('aria-pressed')).toBe('false')
    }
    expect(onAreaPolygon).toHaveBeenCalledTimes(3)
  })

  it('refuses a crossing point and too few points, with a short message', () => {
    const onAreaPolygon = vi.fn(() => null)
    render(<HexMap overview={OVERVIEW} species={null} onAreaPolygon={onAreaPolygon} />)
    startPolygon()
    clickAt(A)
    clickAt(B)
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }))
    expect(screen.getByRole('alert').textContent).toBe('A polygon needs at least 3 different points.')
    clickAt(C)
    clickAt([-120.3, 50.3])
    expect(screen.getByRole('alert').textContent).toBe(CROSSING_POINT)
    expect(drawnPoints()).toEqual([A, B, C])
    expect(onAreaPolygon).not.toHaveBeenCalled()
  })

  it('removes the last point with Backspace or Undo point, and cancels with Esc, leaving the area alone', () => {
    const onAreaPolygon = vi.fn(() => null)
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} onAreaPolygon={onAreaPolygon} />)
    startPolygon()
    clickAt(A)
    clickAt(B)
    clickAt(C)
    press('Backspace')
    expect(drawnPoints()).toEqual([A, B])
    fireEvent.click(screen.getByRole('button', { name: 'Undo point' }))
    expect(drawnPoints()).toEqual([A])
    press('Escape')
    expect(mapState.sources['area-polygon']).toBeUndefined()
    expect(screen.queryByRole('button', { name: 'Finish' })).toBeNull()
    expect(onAreaPolygon).not.toHaveBeenCalled()
  })

  it('leaves the area as it was when a polygon is cancelled with its Cancel button', () => {
    const onAreaPolygon = vi.fn(() => null)
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} onAreaPolygon={onAreaPolygon} />)
    const outline = mapState.sources['area-box']
    expect(outline).toBeDefined()
    startPolygon()
    clickAt(A)
    clickAt(B)
    clickAt(C)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onAreaPolygon).not.toHaveBeenCalled()
    expect(mapState.sources['area-polygon']).toBeUndefined()
    // The area's outline and its one shape are still there.
    expect(mapState.sources['area-box']).toEqual(outline)
    expect(screen.getByText('Your area')).toBeTruthy()
    expect(screen.getByRole('list', { name: 'Shapes in this area' }).textContent).toBe('Box')
  })

  it('keeps drawing with the API\'s words when the shape is refused', () => {
    const refusal = 'The polygon is about 9.9 km², smaller than the minimum of 20.6 km². Draw a larger polygon.'
    render(<HexMap overview={OVERVIEW} species={null} onAreaPolygon={() => refusal} />)
    startPolygon()
    clickAt(A)
    clickAt(B)
    clickAt(C)
    press('Enter')
    expect(screen.getByRole('alert').textContent).toBe(refusal)
    expect(drawnPoints()).toEqual([A, B, C])
  })

  it('cancels when switching to Box or Paste, closing the panel, Clear area, or leaving the page', () => {
    const onClearArea = vi.fn()
    const props = { overview: OVERVIEW, species: null, selectedArea: AREA, onClearArea }
    const { rerender } = render(<HexMap {...props} />)
    // Starts a polygon of one point and checks it shows.
    function oneDrawn() {
      fireEvent.click(screen.getByRole('button', { name: 'Polygon' }))
      clickAt(A)
      expect(drawnPoints()).toEqual([A])
    }
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
    oneDrawn()
    fireEvent.click(screen.getByRole('button', { name: 'Box' }))
    expect(drawnPoints()).toEqual([])
    expect(screen.getByText(DRAW_HINT)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Box' }))
    oneDrawn()
    fireEvent.click(screen.getByRole('tab', { name: 'Paste' }))
    expect(drawnPoints()).toEqual([])
    fireEvent.click(screen.getByRole('tab', { name: 'Draw' }))
    oneDrawn()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(drawnPoints()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
    oneDrawn()
    fireEvent.click(screen.getByRole('button', { name: 'Clear area' }))
    expect(drawnPoints()).toEqual([])
    expect(onClearArea).toHaveBeenCalledTimes(1)
    oneDrawn()
    rerender(<HexMap {...props} rank={{ rank: 'genus', name: 'Turdus', path: [], id: 1 }} rankId="genus-turdus" />)
    expect(drawnPoints()).toEqual([])
  })

  it("gives a new polygon all the area's points, whatever shapes the area has now", () => {
    // 60 boxes (240 points): the polygon replaces them, so all 250 are left
    // and the hint says nothing about the limit.
    const boxes = Array.from({ length: 60 }, (_, i) => boxShapeOf({ west: -125 + i * 0.01, south: 50, east: -124.995 + i * 0.01, north: 50.005 }))
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={{ ...AREA, shapes: boxes }} />)
    startPolygon()
    expect(screen.getByRole('status').textContent).toBe(polygonHint(250))
    expect(polygonHint(250)).toBe(POLYGON_HINT)
    clickAt(A)
    expect(screen.getByRole('status').textContent).toBe(POLYGON_HINT)
  })
})
