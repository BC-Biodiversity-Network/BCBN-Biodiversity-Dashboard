import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The real map (MapLibre) and layers (deck.gl) need a graphics card, which
// the test page does not have. These stand-ins record what the map is given
// instead: the layers handed to deck.gl, the map's onMove handler (to pretend
// the user zoomed) and every fitBounds call.
const mapState = vi.hoisted(() => ({ layers: [], getTooltip: null, onMove: null, fitBounds: null }))

vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react')
  // A stand-in for the map: renders its children and offers fitBounds.
  const Map = React.forwardRef(function Map(props, ref) {
    React.useImperativeHandle(ref, () => ({
      fitBounds: (...args) => mapState.fitBounds(...args),
      getContainer: () => ({ clientWidth: 1280, clientHeight: 800 }),
    }))
    mapState.onMove = props.onMove
    return React.createElement('div', { 'data-testid': 'map' }, props.children)
  })
  // A stand-in for useControl: keeps the overlay's latest layers.
  function useControl() {
    return { setProps: (props) => { mapState.layers = props.layers; mapState.getTooltip = props.getTooltip } }
  }
  return { default: Map, useControl }
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
import HexMap from './HexMap'

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
