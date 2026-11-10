import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The real map (MapLibre) and layers (deck.gl) need a graphics card, which
// the test page does not have. These stand-ins record what the map is given
// instead: the layers handed to deck.gl, the map's onMove handler (to pretend
// the user zoomed) and every fitBounds call, the overlay's click handler, the
// map's onLoad handler, and every easeTo and jumpTo call. bounds is the part
// of the world on screen, [[west, south], [east, north]]. props is every
// prop the map was last given (its mouse and touch handlers, whether a drag
// pans it, its cursor), and sources the GeoJSON of each line it draws, by
// source id (the box, "area-box"; the selected group's outline, "area-group").
const mapState = vi.hoisted(() => ({
  layers: [], getTooltip: null, onMove: null, fitBounds: null, onClick: null, onLoad: null, easeTo: null,
  jumpTo: null, bounds: [[-140, 47], [-113, 61]], props: {}, sources: {}, loaded: true,
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
    }))
    mapState.onMove = props.onMove
    mapState.onLoad = props.onLoad
    mapState.props = props
    mapState.sources = {}
    return React.createElement('div', { 'data-testid': 'map' }, props.children)
  })
  // Stand-ins for a GeoJSON source and its layer: the source records its data.
  function Source({ id, data, children }) {
    mapState.sources[id] = data
    return React.createElement(React.Fragment, null, children)
  }
  function Layer() {
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

import { boxCells, groupOutline } from './area'
import HexMap, { DRAW_HINT, DRAW_UNAVAILABLE, FADED_SHARE, outlineBounds, overviewLayerId } from './HexMap'
import { pickResolution, zoomShowingResolution } from './zoomTiers'

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

  it('fades the outline out when zoomed so far out that the hexagons get tiny', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    const lineColour = () => mapState.layers[0].props.getLineColor
    // Zoom 3.85: resolution 4 hexagons about 16.5 pixels wide.
    act(() => mapState.onMove({ viewState: { zoom: 3.85 } }))
    expect(lineColour()).toEqual([255, 255, 255, 191])
    // Zoom 2.5: about 6.5 pixels wide, so no outline at all.
    act(() => mapState.onMove({ viewState: { zoom: 2.5 } }))
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

  it('shows the year control under the scale toggle, then the Draw a box button', () => {
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

describe('drawing a box', () => {
  // A mouse event on the map at [lng, lat], as react-map-gl gives it.
  function at(lng, lat) {
    return { lngLat: { lng, lat }, preventDefault: () => {} }
  }

  it('offers "Draw a box" on the overview and a rank map, greyed out with a reason on a species map', () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    expect(screen.getByRole('button', { name: 'Draw a box' }).disabled).toBe(false)
    rerender(<HexMap overview={OVERVIEW} species={ROBIN} />)
    const button = screen.getByRole('button', { name: 'Draw a box' })
    expect(button.disabled).toBe(true)
    expect(button.closest('[title]').getAttribute('title')).toBe(DRAW_UNAVAILABLE)
    expect(document.getElementById(button.getAttribute('aria-describedby')).textContent).toBe(DRAW_UNAVAILABLE)
  })

  it('stops the map panning, shows the hint and the crosshair, and hands on the box and the resolution', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    expect(mapState.props.dragPan).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    expect(mapState.props.dragPan).toBe(false)
    expect(mapState.props.touchZoomRotate).toBe(false)
    expect(mapState.props.boxZoom).toBe(false)
    expect(mapState.props.cursor).toBe('crosshair')
    expect(screen.getByText(DRAW_HINT)).toBeTruthy()
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    act(() => mapState.props.onMouseMove(at(-120.4, 50.6)))
    // The box shows while it is dragged.
    expect(mapState.sources['area-box'].geometry.coordinates[0]).toEqual([-120.4, 50.6])
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    const resolution = pickResolution(4.5, null)
    expect(onAreaDrawn).toHaveBeenCalledWith({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, resolution)
    expect(mapState.props.dragPan).toBe(true)
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
  })

  it('draws with one finger too', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    act(() => mapState.props.onTouchStart({ ...at(-120.6, 50.55), points: [{}] }))
    act(() => mapState.props.onTouchMove({ ...at(-120.1, 50.8), points: [{}] }))
    act(() => mapState.props.onTouchEnd({ points: [] }))
    expect(onAreaDrawn).toHaveBeenCalledWith({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, expect.any(Number))
  })

  it('cancels with Esc or the button, drawing nothing', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} onAreaDrawn={onAreaDrawn} />)
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
    expect(mapState.props.dragPan).toBe(true)
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel drawing' }))
    expect(screen.queryByText(DRAW_HINT)).toBeNull()
    expect(onAreaDrawn).not.toHaveBeenCalled()
  })

  it('ends drawing when the map turns to another page (Back, a link, a panel), and not otherwise', () => {
    const props = { overview: OVERVIEW, species: null }
    const { rerender } = render(<HexMap {...props} />)
    // Starts drawing with the button and checks it is on.
    function startDrawing() {
      fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
      expect(screen.getByText(DRAW_HINT)).toBeTruthy()
      expect(mapState.props.dragPan).toBe(false)
    }
    // Checks drawing is off: the button, the hint and the map's dragging.
    function expectNotDrawing() {
      expect(screen.getByRole('button', { name: 'Draw a box' })).toBeTruthy()
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
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    act(() => mapState.props.onMouseDown(at(-120.1, 50.8)))
    act(() => mapState.props.onMouseUp(at(-120.6, 50.55)))
    act(() => mapState.onClick({ object: OVERVIEW.rows[4][0] }))
    expect(onCellClick).not.toHaveBeenCalled()
  })

  // An area holding the overview's resolution 6 hexagon (8628c9b47ffffff)
  // and none of the overview's other hexagons, with its outline.
  const AREA = { box: { west: -130.4, south: 48.8, east: -130.25, north: 48.9 }, res: 6,
    cells: ['8628c9b47ffffff'], outline: groupOutline(['8628c9b47ffffff']) }

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
    // The selected hexagon, the resolution 7 one inside it and the
    // resolution 4 and 5 ones that hold it keep their colour.
    for (const r of [4, 5, 6, 7]) {
      expect(fillOf(overviewLayerId(r), OVERVIEW.rows[r][0])).toEqual(usual[r])
    }
    // A hexagon outside the area keeps its colour at a fifth of its opacity.
    const faded = fillOf(overviewLayerId(6), elsewhere)
    expect(faded.slice(0, 3)).toEqual(usual[6].slice(0, 3))
    expect(faded[3]).toBe(Math.round(usual[6][3] * FADED_SHARE))
    expect(FADED_SHARE).toBe(0.2)
    // deck.gl is told the colours changed.
    expect(mapState.layers.find((layer) => layer.id === overviewLayerId(6)).props.updateTriggers.getFillColor)
      .toEqual(['species', '6:-130.4,48.8,-130.25,48.9'])
  })

  it('draws one outline around the whole group and the dashed box, and no outline per hexagon', () => {
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} />)
    expect(mapState.sources['area-group']).toBe(AREA.outline)
    expect(mapState.sources['area-box'].geometry.coordinates).toEqual(
      [[-130.4, 48.8], [-130.25, 48.8], [-130.25, 48.9], [-130.4, 48.9], [-130.4, 48.8]])
    expect(mapState.layers.some((layer) => layer.id.startsWith('selected-area'))).toBe(false)
    expect(mapState.layers.every((layer) => layer.props.stroked === false || layer.props.filled !== false)).toBe(true)
  })

  it('draws a new box anywhere while an area is selected: outside it, over part of it, or too small', () => {
    const onAreaDrawn = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={AREA} onAreaDrawn={onAreaDrawn} />)
    // Draws one box from corner to corner and returns what was handed on.
    function draw(from, to) {
      fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
      act(() => mapState.props.onMouseDown(at(...from)))
      act(() => mapState.props.onMouseMove(at(...to)))
      // The new box shows while it is dragged, instead of the old one.
      expect(mapState.sources['area-box'].geometry.coordinates[2]).toEqual(to)
      expect(mapState.sources['area-group']).toBeUndefined()
      act(() => mapState.props.onMouseUp(at(...to)))
      expect(mapState.props.dragPan).toBe(true)
      return onAreaDrawn.mock.calls.at(-1)[0]
    }
    // Fully outside the area (around Kamloops, far from it).
    expect(draw([-120.6, 50.55], [-120.1, 50.8])).toEqual({ west: -120.6, south: 50.55, east: -120.1, north: 50.8 })
    // Over part of it.
    expect(draw([-130.3, 48.85], [-130.0, 49.1])).toEqual({ west: -130.3, south: 48.85, east: -130, north: 49.1 })
    // Too small: still handed on, for App to keep the old area and say why.
    expect(draw([-125, 52], [-124.999, 52.001])).toEqual({ west: -125, south: 52, east: -124.999, north: 52.001 })
    expect(onAreaDrawn).toHaveBeenCalledTimes(3)
    // Once drawing ends, the selected area shows again.
    expect(mapState.sources['area-group']).toBe(AREA.outline)
  })

  it('fits the whole group in view when the page opens on an area', () => {
    // The box around Kamloops at resolution 6: 45 hexagons, reaching past it.
    const box = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
    const cells = boxCells(box, 6)
    const kamloops = { box, res: 6, cells, outline: groupOutline(cells) }
    mapState.fitBounds = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} selectedArea={kamloops} />)
    act(() => mapState.onLoad())
    const [[[west, south], [east, north]]] = mapState.fitBounds.mock.calls[0]
    expect([[west, south], [east, north]]).toEqual(outlineBounds(kamloops.outline))
    expect(west).toBeLessThan(box.west)
    expect(south).toBeLessThan(box.south)
    expect(east).toBeGreaterThan(box.east)
    expect(north).toBeGreaterThan(box.north)
  })

  it('fades nothing with no area selected', () => {
    render(<HexMap overview={OVERVIEW} species={null} />)
    expect(mapState.layers.find((layer) => layer.id === overviewLayerId(6)).props.updateTriggers.getFillColor)
      .toEqual(['species', null])
    expect(mapState.sources['area-group']).toBeUndefined()
  })

  it('offers Clear area while an area exists, and Show area while its panel is closed, beside Draw a box', () => {
    const onClear = vi.fn()
    const onShow = vi.fn()
    const { rerender } = render(<HexMap overview={OVERVIEW} species={null} />)
    expect(screen.queryByRole('button', { name: 'Clear area' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
    rerender(<HexMap overview={OVERVIEW} species={null} onClearArea={onClear} onShowArea={onShow} />)
    const buttons = [...document.querySelectorAll('.draw-box-buttons button')].map((button) => button.textContent)
    expect(buttons).toEqual(['Draw a box', 'Show area', 'Clear area'])
    fireEvent.click(screen.getByRole('button', { name: 'Show area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear area' }))
    expect(onShow).toHaveBeenCalledTimes(1)
    expect(onClear).toHaveBeenCalledTimes(1)
    // Not while drawing a new box.
    fireEvent.click(screen.getByRole('button', { name: 'Draw a box' }))
    expect(screen.queryByRole('button', { name: 'Clear area' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
  })

  it('shows neither on a single species map, where the area is not shown', () => {
    stubHexes(() => ({ body: hexes('59PMB', 4, ['8428c9bffffffff'], [3]) }))
    render(<HexMap overview={OVERVIEW} species={ROBIN} onClearArea={vi.fn()} onShowArea={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Clear area' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show area' })).toBeNull()
  })

  it('shows the message about a box too small or too big, which its x dismisses', () => {
    const onDismiss = vi.fn()
    render(<HexMap overview={OVERVIEW} species={null} areaMessage="This box is about 9.9 km², too small."
      onDismissAreaMessage={onDismiss} />)
    expect(screen.getByRole('alert').textContent).toContain('This box is about 9.9 km², too small.')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss this message' }))
    expect(onDismiss).toHaveBeenCalled()
  })
})
