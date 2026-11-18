import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The species map page with the real address handling (useLocationSearch on
// the test page's own history, so new entries, replaced entries and the back
// button behave as in a browser) and stand-ins for its parts: the map
// records its props, the search box picks the robin, the species card, the
// rank card and the hexagon panel are a line of text and buttons.
const seen = vi.hoisted(() => ({ map: null }))
// The list options the stand-in panel and rank card change to.
const PANEL_LIST = vi.hoisted(() => ({ q: 'bee', sort: 'english', status: ['red', 'none'], page: 2 }))
const CARD_LIST = vi.hoisted(() => ({ q: 'thrush', sort: 'scientific', status: ['yellow'], page: 3 }))
// The genus Turdus, as a rank card opens it.
const TURDUS = vi.hoisted(() => ({ rank: 'genus', name: 'Turdus', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
  { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
] }))

vi.mock('./HexMap', () => ({
  // A stand-in map that only records its props.
  default: function HexMap(props) {
    seen.map = props
    return null
  },
}))
vi.mock('./SearchBox', () => ({
  // A stand-in search box: one button that picks the robin, one that picks
  // the genus Turdus.
  default: function SearchBox({ onSelect, onSelectRank }) {
    return (
      <>
        <button type="button" onClick={() => onSelect({ specieskey: '59PMB', col_name: 'Turdus migratorius' })}>Pick the robin</button>
        <button type="button" onClick={() => onSelectRank(TURDUS)}>Pick the genus Turdus</button>
      </>
    )
  },
}))
vi.mock('./SpeciesCard', () => ({
  // A stand-in card: its key, a button that closes it, and the genus Turdus
  // in its classification line.
  default: function SpeciesCard({ requestedKey, onClose, onOpenRank }) {
    return (
      <div className="stand-in-card">
        <p>Card of {requestedKey}</p>
        <button type="button" onClick={onClose}>Close the card</button>
        <button type="button" onClick={() => onOpenRank(TURDUS)}>Open the genus from the card</button>
      </div>
    )
  },
}))
vi.mock('./HexagonPanel', () => ({
  // A stand-in panel: which hexagon on which map, its x, and a species in
  // its list.
  default: function HexagonPanel({ cell, species, rank, list, onListChange, onClose, onOpenSpecies, onShowAllSpecies }) {
    const map = species ? `the map of ${species.key}` : rank ? `the map of ${rank.rank} ${rank.name}` : 'the overview'
    return (
      <div className="stand-in-panel">
        <p>Panel of {cell} on {map}</p>
        <button type="button" onClick={onClose}>Close the panel</button>
        <button type="button" onClick={() => onOpenSpecies('CCCC')}>Open the bumble bee from the list</button>
        <button type="button" onClick={onShowAllSpecies}>Show all species</button>
        <button type="button" onClick={() => onListChange(PANEL_LIST)}>Filter the panel's list</button>
        <p className="stand-in-panel-list">{JSON.stringify(list)}</p>
      </div>
    )
  },
}))
vi.mock('./AreaPanel', () => ({
  // A stand-in area panel: its shapes (as area text), resolution and map,
  // its x, a species in its list, and a change to its list.
  default: function AreaPanel({ area, cellCount, rank, list, onListChange, onClose, onOpenSpecies }) {
    const map = rank ? `the map of ${rank.rank} ${rank.name}` : 'the overview'
    const text = area.shapes.map(({ box, ring }) => (box ? `b:${[box.west, box.south, box.east, box.north].join(',')}`
      : `p:${ring.flat().join(',')}`)).join(';')
    return (
      <div className="stand-in-area">
        <p>Area {text} at {area.res} ({cellCount} hexagons) on {map}</p>
        <button type="button" onClick={onClose}>Close the area</button>
        <button type="button" onClick={() => onOpenSpecies('CCCC')}>Open the bumble bee from the area</button>
        <button type="button" onClick={() => onListChange(PANEL_LIST)}>Filter the area's list</button>
        <p className="stand-in-area-list">{JSON.stringify(list)}</p>
      </div>
    )
  },
}))
vi.mock('./RankCard', () => ({
  // A stand-in rank card: its rank, its x, the genus Turdus as the next
  // rank down, and the robin in its list.
  default: function RankCard({ rankRef, years, list, onListChange, onClose, onOpenSpecies, onOpenRank }) {
    return (
      <div className="stand-in-rank-card">
        <p>Card of {rankRef.rank} {rankRef.name}</p>
        <button type="button" onClick={onClose}>Close the rank</button>
        <button type="button" onClick={() => onOpenRank(TURDUS)}>Open the genus Turdus</button>
        <button type="button" onClick={() => onOpenSpecies('59PMB')}>Open the robin from the rank</button>
        <button type="button" onClick={() => onListChange(CARD_LIST)}>Filter the rank's list</button>
        <p className="stand-in-card-list">{JSON.stringify(list)}</p>
        <p className="stand-in-card-years">{JSON.stringify(years)}</p>
      </div>
    )
  },
}))
vi.mock('./loadHexData', () => ({
  // The overview files, empty.
  loadHexData: async () => [],
}))

// Imported after the stand-ins above, which vitest puts in place first.
import App from './App'
import { boxCells } from './area'
import { areaText as areaTextOfShapes, boxShapeOf, shapesCells } from './areaShapes'
import { PANEL_FROM_CLICK, useLocationSearch } from './route'

const CELL_A = '872800b30ffffff'
const CELL_B = '8728c9b40ffffff'

// The family Turdidae and the genus Turdus, as the address names them.
const TURDIDAE_SEARCH = '?rank=family&kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes&family=Turdidae'
const TURDUS_SEARCH = '?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes&family=Turdidae'
  + '&genus=Turdus'

// The page as the site shows it: the address read and changed through the
// browser's history.
function Page() {
  const [search, navigate] = useLocationSearch()
  return <App search={search} navigate={navigate} />
}

// The page's history calls since it opened: how many entries it added and
// how many it replaced. (The test page's history is shared by every test,
// and a new entry drops any entries ahead of it, so history.length alone
// cannot tell the two apart.)
const history = { pushes: null, replaces: null, backs: null, goes: null }

// Opens the page at a query string, as a fresh visit, and lets it settle.
async function openAt(query) {
  window.history.replaceState(null, '', `/${query}`)
  render(<Page />)
  await act(async () => {})
  history.pushes.mockClear()
  history.replaces.mockClear()
  history.backs.mockClear()
  history.goes.mockClear()
}

// Opens the page at query as a new entry after previous, so there is
// somewhere for Back to go, and lets it settle.
async function openAfter(previous, query) {
  window.history.replaceState(null, '', `/${previous}`)
  window.history.pushState(null, '', `/${query}`)
  await openAt(query)
}

// Closes the panel with its x and waits for the address to be expectedSearch
// (the x may go back, which the page follows a moment later).
async function closeWithX(expectedSearch) {
  fireEvent.click(screen.getByRole('button', { name: 'Close the panel' }))
  await waitFor(() => expect(window.location.search).toBe(expectedSearch))
  await act(async () => {})
}

// How many entries the page has added and replaced since it opened.
function calls() {
  return { added: history.pushes.mock.calls.length, replaced: history.replaces.mock.calls.length }
}

// Clicks a hexagon on the map.
function clickHexagon(cell) {
  act(() => seen.map.onCellClick(cell))
}

// Presses the browser's back button and waits for the page to follow.
async function pressBack(expectedSearch) {
  act(() => window.history.back())
  await waitFor(() => expect(window.location.search).toBe(expectedSearch))
  await act(async () => {})
}

// The panel's line, or null when no panel is shown.
function panelText() {
  return document.querySelector('.stand-in-panel p')?.textContent ?? null
}

// Whether the species card is on screen (it stays loaded but hidden while
// a hexagon's panel covers it).
function cardShown() {
  const holder = document.querySelector('.card-holder:not(.area-holder)')
  return Boolean(holder) && !holder.hidden
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
  history.pushes = vi.spyOn(window.history, 'pushState')
  history.replaces = vi.spyOn(window.history, 'replaceState')
  history.backs = vi.spyOn(window.history, 'back')
  history.goes = vi.spyOn(window.history, 'go')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the hexagon panel and the address', () => {
  it('opens with a new address entry on the first click, and outlines the hexagon', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    expect(window.location.search).toBe(`?cell=${CELL_A}`)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.selectedCell).toBe(CELL_A)
  })

  it('replaces that entry for further clicks while the panel is open', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    clickHexagon(CELL_B)
    expect(window.location.search).toBe(`?cell=${CELL_B}`)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    clickHexagon(CELL_B)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(panelText()).toBe(`Panel of ${CELL_B} on the overview`)
  })

  it('closes on Back and returns to the overview, after any number of clicks', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    clickHexagon(CELL_B)
    await pressBack('')
    expect(panelText()).toBeNull()
    expect(seen.map.selectedCell).toBeNull()
  })

  it('covers the species card on a species map, leads with the species, and Back brings the card back', async () => {
    await openAt('?species=59PMB')
    expect(cardShown()).toBe(true)
    clickHexagon(CELL_A)
    expect(window.location.search).toBe(`?species=59PMB&cell=${CELL_A}`)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the map of 59PMB`)
    expect(cardShown()).toBe(false)
    expect(seen.map.species.key).toBe('59PMB')
    await pressBack('?species=59PMB')
    expect(panelText()).toBeNull()
    expect(cardShown()).toBe(true)
  })

  it('goes back with the x after a click on the overview, leaving no dead entry: the next Back really goes back',
    async () => {
      await openAfter('?species=59PMB', '')
      clickHexagon(CELL_A)
      clickHexagon(CELL_B)
      await closeWithX('')
      expect(calls()).toEqual({ added: 1, replaced: 1 })
      expect(history.backs).toHaveBeenCalledTimes(1)
      expect(panelText()).toBeNull()
      expect(seen.map.selectedCell).toBeNull()
      await pressBack('?species=59PMB')
      expect(screen.getByText('Card of 59PMB')).toBeTruthy()
    })

  it('goes back with the x after a click on a species map, showing the card again: the next Back really goes back',
    async () => {
      await openAfter('', '?species=59PMB')
      clickHexagon(CELL_A)
      await closeWithX('?species=59PMB')
      expect(calls()).toEqual({ added: 1, replaced: 0 })
      expect(history.backs).toHaveBeenCalledTimes(1)
      expect(panelText()).toBeNull()
      expect(cardShown()).toBe(true)
      expect(screen.getByText('Card of 59PMB')).toBeTruthy()
      await pressBack('')
      expect(cardShown()).toBe(false)
    })

  it('goes back with the x when Back has returned to a panel opened by a click', async () => {
    await openAfter('?species=59PMB', '')
    clickHexagon(CELL_A)
    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the list' }))
    await pressBack(`?cell=${CELL_A}`)
    await closeWithX('')
    expect(history.backs).toHaveBeenCalledTimes(2)
    await pressBack('?species=59PMB')
  })

  it('closes in place with the x when the panel came from a shared link, on the overview', async () => {
    await openAt(`?cell=${CELL_A}`)
    fireEvent.click(screen.getByRole('button', { name: 'Close the panel' }))
    expect(window.location.search).toBe('')
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(history.backs).not.toHaveBeenCalled()
    expect(panelText()).toBeNull()
  })

  it('closes in place with the x when the panel came from a shared link, also after further clicks', async () => {
    await openAt(`?species=59PMB&cell=${CELL_A}`)
    clickHexagon(CELL_B)
    expect(window.location.search).toBe(`?species=59PMB&cell=${CELL_B}`)
    fireEvent.click(screen.getByRole('button', { name: 'Close the panel' }))
    expect(window.location.search).toBe('?species=59PMB')
    expect(calls()).toEqual({ added: 0, replaced: 2 })
    expect(history.backs).not.toHaveBeenCalled()
    expect(cardShown()).toBe(true)
  })

  it('opens a species from the list as a new entry, and Back (or the link) returns to the list', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the list' }))
    expect(window.location.search).toBe('?species=CCCC')
    expect(calls()).toEqual({ added: 2, replaced: 0 })
    expect(panelText()).toBeNull()
    expect(screen.getByText('Card of CCCC')).toBeTruthy()
    expect(seen.map.species.key).toBe('CCCC')
    await pressBack(`?cell=${CELL_A}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.species).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the list' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to this hexagon\'s species' }))
    await waitFor(() => expect(window.location.search).toBe(`?cell=${CELL_A}`))
    await act(async () => {})
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(screen.queryByRole('button', { name: 'Back to this hexagon\'s species' })).toBeNull()
  })

  it('returns from a species opened from the list to the panel on the species map it came from', async () => {
    await openAt('?species=59PMB')
    clickHexagon(CELL_A)
    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the list' }))
    expect(window.location.search).toBe('?species=CCCC')
    expect(cardShown()).toBe(true)
    await pressBack(`?species=59PMB&cell=${CELL_A}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the map of 59PMB`)
    expect(seen.map.species.key).toBe('59PMB')
  })

  it('shows no link back to a list when a card was not opened from one', async () => {
    await openAt('?species=59PMB')
    expect(screen.queryByRole('button', { name: 'Back to this hexagon\'s species' })).toBeNull()
  })

  it('closes the panel when a species is picked in the search box, replacing the entry as before', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    fireEvent.click(screen.getByRole('button', { name: 'Pick the robin' }))
    expect(window.location.search).toBe('?species=59PMB')
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(panelText()).toBeNull()
    expect(cardShown()).toBe(true)
  })

  it('switches from a species map\'s panel to every species on the overview as a new entry, and Back returns',
    async () => {
      await openAfter('', '?species=59PMB')
      clickHexagon(CELL_A)
      fireEvent.click(screen.getByRole('button', { name: 'Show all species' }))
      expect(window.location.search).toBe(`?cell=${CELL_A}`)
      expect(calls()).toEqual({ added: 2, replaced: 0 })
      expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
      expect(seen.map.species).toBeNull()
      expect(seen.map.selectedCell).toBe(CELL_A)
      await pressBack(`?species=59PMB&cell=${CELL_A}`)
      expect(panelText()).toBe(`Panel of ${CELL_A} on the map of 59PMB`)
      expect(seen.map.species.key).toBe('59PMB')
      // The species panel's entry came from a click, so its x still goes back.
      await closeWithX('?species=59PMB')
      expect(cardShown()).toBe(true)
      await pressBack('')
    })

  it('closes the overview\'s panel reached by "Show all" in place, and Back then returns to the species panel',
    async () => {
      await openAfter('', '?species=59PMB')
      clickHexagon(CELL_A)
      fireEvent.click(screen.getByRole('button', { name: 'Show all species' }))
      await closeWithX('')
      expect(calls()).toEqual({ added: 2, replaced: 1 })
      expect(history.backs).not.toHaveBeenCalled()
      expect(panelText()).toBeNull()
      await pressBack(`?species=59PMB&cell=${CELL_A}`)
      expect(panelText()).toBe(`Panel of ${CELL_A} on the map of 59PMB`)
    })

  it('opens the panel a shared link names, on the overview or a species map', async () => {
    await openAt(`?species=59PMB&cell=${CELL_B}`)
    expect(panelText()).toBe(`Panel of ${CELL_B} on the map of 59PMB`)
    expect(seen.map.selectedCell).toBe(CELL_B)
    expect(cardShown()).toBe(false)
  })
})

describe('a rank and the address', () => {
  // The rank card's line, or null when it is not on screen.
  function rankCardText() {
    const holder = document.querySelector('.stand-in-rank-card')?.closest('.card-holder')
    return holder && !holder.hidden ? holder.querySelector('p').textContent : null
  }

  it('opens the rank a shared link names, with its card and its map', async () => {
    await openAt(TURDUS_SEARCH)
    expect(rankCardText()).toBe('Card of genus Turdus')
    expect(seen.map.rank).toMatchObject(TURDUS)
    expect(seen.map.rankId).toBe('Animalia|Chordata|Aves|Passeriformes|Turdidae|Turdus|genus')
    expect(seen.map.species).toBeNull()
  })

  it('closes in place with the x on the card or on the map when it came from a shared link', async () => {
    await openAt(TURDUS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(rankCardText()).toBeNull()
    expect(seen.map.rank).toBeNull()

    cleanup()
    await openAt(TURDIDAE_SEARCH)
    act(() => seen.map.onCloseRank())
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(seen.map.rank).toBeNull()
  })

  it('goes back with the x when the rank was opened from the plain map, leaving no dead entry', async () => {
    window.history.replaceState(null, '', '/')
    window.history.pushState({ rankOpenedFrom: '' }, '', `/${TURDUS_SEARCH}`)
    render(<Page />)
    await act(async () => {})
    history.pushes.mockClear()
    history.replaces.mockClear()
    history.backs.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await waitFor(() => expect(window.location.search).toBe(''))
    await act(async () => {})
    expect(history.backs).toHaveBeenCalledTimes(1)
    expect(calls()).toEqual({ added: 0, replaced: 0 })
    expect(seen.map.rank).toBeNull()
  })

  it('opens the next rank down as a new entry; Back returns; its x then returns to all species in place', async () => {
    await openAt(TURDIDAE_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Open the genus Turdus' }))
    await act(async () => {})
    expect(window.location.search).toBe(TURDUS_SEARCH)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(rankCardText()).toBe('Card of genus Turdus')
    expect(seen.map.rank.name).toBe('Turdus')
    const firstId = seen.map.rank.id

    await pressBack(TURDIDAE_SEARCH)
    expect(rankCardText()).toBe('Card of family Turdidae')
    expect(seen.map.rank.name).toBe('Turdidae')
    // Each opening is a new id, so the map moves to the rank again.
    expect(seen.map.rank.id).not.toBe(firstId)

    fireEvent.click(screen.getByRole('button', { name: 'Open the genus Turdus' }))
    await act(async () => {})
    history.backs.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(history.backs).not.toHaveBeenCalled()
    expect(seen.map.rank).toBeNull()
  })

  it('opens a species from the rank card as a new entry, with a link back to the rank', async () => {
    await openAt(TURDUS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Open the robin from the rank' }))
    await act(async () => {})
    expect(window.location.search).toBe('?species=59PMB')
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(screen.getByText('Card of 59PMB')).toBeTruthy()
    expect(seen.map.rank).toBeNull()
    expect(seen.map.species.key).toBe('59PMB')
    const back = screen.getByRole('button', { name: 'Back to genus Turdus' })
    expect(back.querySelector('i').textContent).toBe('Turdus')
    fireEvent.click(back)
    await waitFor(() => expect(window.location.search).toBe(TURDUS_SEARCH))
    await act(async () => {})
    expect(rankCardText()).toBe('Card of genus Turdus')
    expect(seen.map.rank.name).toBe('Turdus')
    expect(screen.queryByRole('button', { name: 'Back to genus Turdus' })).toBeNull()
  })

  it('opens a hexagon on the rank map over the card, keeping the rank in the address; the x goes back', async () => {
    await openAt(TURDUS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    expect(window.location.search).toBe(`${TURDUS_SEARCH}&cell=${CELL_A}`)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the map of genus Turdus`)
    expect(rankCardText()).toBeNull()
    clickHexagon(CELL_B)
    await act(async () => {})
    expect(window.location.search).toBe(`${TURDUS_SEARCH}&cell=${CELL_B}`)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    await closeWithX(TURDUS_SEARCH)
    expect(panelText()).toBeNull()
    expect(rankCardText()).toBe('Card of genus Turdus')
  })

  it('opens a shared link to a hexagon on a rank map, and its x closes the panel in place', async () => {
    await openAt(`${TURDUS_SEARCH}&cell=${CELL_A}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the map of genus Turdus`)
    await closeWithX(TURDUS_SEARCH)
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(rankCardText()).toBe('Card of genus Turdus')
  })

  it('keeps an open hexagon when the rank closes, showing every species in it', async () => {
    await openAt(`${TURDUS_SEARCH}&cell=${CELL_A}`)
    act(() => seen.map.onCloseRank())
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}`)
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.rank).toBeNull()
  })

  it('switches from the rank\'s panel to every species in the hexagon as a new entry, and Back returns', async () => {
    await openAt(`${TURDUS_SEARCH}&cell=${CELL_A}`)
    fireEvent.click(screen.getByRole('button', { name: 'Show all species' }))
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}`)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    await pressBack(`${TURDUS_SEARCH}&cell=${CELL_A}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the map of genus Turdus`)
  })

  it('opens a rank picked in the search box on the plain map as a new entry; its x then acts like Back', async () => {
    await openAt('')
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    expect(window.location.search).toBe(TURDUS_SEARCH)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(rankCardText()).toBe('Card of genus Turdus')
    expect(seen.map.rank.name).toBe('Turdus')
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await waitFor(() => expect(window.location.search).toBe(''))
    await act(async () => {})
    expect(history.backs).toHaveBeenCalledTimes(1)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(seen.map.rank).toBeNull()
    expect(rankCardText()).toBeNull()

    // The same with the x on the map's "Showing ..." label.
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    act(() => seen.map.onCloseRank())
    await waitFor(() => expect(window.location.search).toBe(''))
    await act(async () => {})
    expect(history.backs).toHaveBeenCalledTimes(2)
    expect(calls()).toEqual({ added: 2, replaced: 0 })
  })

  it('closes in place a rank picked on the plain map once a hexagon is open, keeping the hexagon', async () => {
    await openAt('')
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    clickHexagon(CELL_A)
    await act(async () => {})
    act(() => seen.map.onCloseRank())
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}`)
    expect(history.backs).not.toHaveBeenCalled()
    expect(calls()).toEqual({ added: 2, replaced: 1 })
  })

  it('opens a rank picked in the search box on a species map as a new entry: Back returns to the species, '
    + 'and the x returns to all species in place', async () => {
    await openAt('?species=59PMB')
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    expect(window.location.search).toBe(TURDUS_SEARCH)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(seen.map.species).toBeNull()
    expect(seen.map.rank.name).toBe('Turdus')
    await pressBack('?species=59PMB')
    expect(screen.getByText('Card of 59PMB')).toBeTruthy()
    expect(seen.map.rank).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    history.backs.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(history.backs).not.toHaveBeenCalled()
  })

  it('opens a rank from the species card\'s classification line as a new entry, and Back returns to the card',
    async () => {
      await openAt('?species=59PMB')
      fireEvent.click(screen.getByRole('button', { name: 'Open the genus from the card' }))
      await act(async () => {})
      expect(window.location.search).toBe(TURDUS_SEARCH)
      expect(calls()).toEqual({ added: 1, replaced: 0 })
      expect(rankCardText()).toBe('Card of genus Turdus')
      await pressBack('?species=59PMB')
      expect(screen.getByText('Card of 59PMB')).toBeTruthy()
      expect(rankCardText()).toBeNull()
    })

  it('only moves the map again when the rank already shown is picked, adding no entry', async () => {
    await openAt(TURDUS_SEARCH)
    const firstId = seen.map.rank.id
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    expect(calls()).toEqual({ added: 0, replaced: 0 })
    expect(seen.map.rank.id).not.toBe(firstId)
    expect(rankCardText()).toBe('Card of genus Turdus')
  })

  it('closes the rank when a species is picked in the search box, replacing the entry', async () => {
    await openAt(TURDUS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Pick the robin' }))
    await act(async () => {})
    expect(window.location.search).toBe('?species=59PMB')
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(rankCardText()).toBeNull()
    expect(seen.map.rank).toBeNull()
    expect(screen.queryByRole('button', { name: 'Back to genus Turdus' })).toBeNull()
  })
})

describe('the species lists and the address', () => {
  const PANEL_LIST_SEARCH = 'cell_q=bee&cell_sort=english&cell_status=red&cell_status=none&cell_page=2'
  const CARD_LIST_SEARCH = 'q=thrush&sort=scientific&status=yellow&page=3'

  // The list options the stand-in panel or card was given.
  function listGiven(selector) {
    return JSON.parse(document.querySelector(selector).textContent)
  }

  it('changes a hexagon panel\'s list in place, keeping the entry\'s mark, so the x still goes back', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Filter the panel\'s list' }))
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}&${PANEL_LIST_SEARCH}`)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(window.history.state).toEqual(PANEL_FROM_CLICK)
    expect(listGiven('.stand-in-panel-list')).toEqual(PANEL_LIST)
    await closeWithX('')
    expect(history.backs).toHaveBeenCalledTimes(1)
  })

  it('keeps the order and statuses for the next hexagon clicked, without the name filter or page', async () => {
    await openAt(`?cell=${CELL_A}&${PANEL_LIST_SEARCH}`)
    clickHexagon(CELL_B)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_B}&cell_sort=english&cell_status=red&cell_status=none`)
    expect(listGiven('.stand-in-panel-list')).toEqual({ q: '', sort: 'english', status: ['red', 'none'], page: 1 })
  })

  it('opens a shared link to a filtered list with its options', async () => {
    await openAt(`${TURDUS_SEARCH}&${CARD_LIST_SEARCH}`)
    expect(listGiven('.stand-in-card-list')).toEqual(CARD_LIST)
    cleanup()
    await openAt(`?cell=${CELL_A}&${PANEL_LIST_SEARCH}`)
    expect(listGiven('.stand-in-panel-list')).toEqual(PANEL_LIST)
  })

  it('changes a rank card\'s list in place, keeping the rank\'s history state, and keeps it under a hexagon',
    async () => {
      window.history.replaceState(null, '', '/')
      window.history.pushState({ rankOpenedFrom: '' }, '', `/${TURDUS_SEARCH}`)
      render(<Page />)
      await act(async () => {})
      history.pushes.mockClear()
      history.replaces.mockClear()
      fireEvent.click(screen.getByRole('button', { name: 'Filter the rank\'s list' }))
      await act(async () => {})
      expect(window.location.search).toBe(`${TURDUS_SEARCH}&${CARD_LIST_SEARCH}`)
      expect(calls()).toEqual({ added: 0, replaced: 1 })
      expect(window.history.state).toEqual({ rankOpenedFrom: '' })

      clickHexagon(CELL_A)
      await act(async () => {})
      expect(window.location.search).toBe(`${TURDUS_SEARCH}&${CARD_LIST_SEARCH}&cell=${CELL_A}`)
      await closeWithX(`${TURDUS_SEARCH}&${CARD_LIST_SEARCH}`)
      expect(listGiven('.stand-in-card-list')).toEqual(CARD_LIST)
    })

  it('opens the next rank down with the card\'s order and statuses, and closes the rank without its list',
    async () => {
      await openAt(`${TURDIDAE_SEARCH}&${CARD_LIST_SEARCH}`)
      fireEvent.click(screen.getByRole('button', { name: 'Open the genus Turdus' }))
      await act(async () => {})
      expect(window.location.search).toBe(`${TURDUS_SEARCH}&sort=scientific&status=yellow`)
      fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
      await act(async () => {})
      expect(window.location.search).toBe('')
    })
})

describe('the year range and the address', () => {
  // The years the stand-in rank card was given.
  function cardYears() {
    return JSON.parse(document.querySelector('.stand-in-card-years').textContent)
  }

  it('gives a rank page\'s card the years of its address, and changes them in place', async () => {
    window.history.replaceState(null, '', '/')
    window.history.pushState({ rankOpenedFrom: '' }, '', `/${TURDUS_SEARCH}&year_from=2000&year_to=2020`)
    render(<Page />)
    await act(async () => {})
    history.pushes.mockClear()
    history.replaces.mockClear()
    expect(cardYears()).toEqual({ from: 2000, to: 2020 })
    expect(seen.map.years).toEqual({ from: 2000, to: 2020 })
    act(() => seen.map.onYearsChange({ from: 2010, to: null }))
    await act(async () => {})
    expect(window.location.search).toBe(`${TURDUS_SEARCH}&year_from=2010`)
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(window.history.state).toEqual({ rankOpenedFrom: '' })
    expect(cardYears()).toEqual({ from: 2010, to: null })
    act(() => seen.map.onYearsChange(null))
    await act(async () => {})
    expect(window.location.search).toBe(TURDUS_SEARCH)
    expect(cardYears()).toBeNull()
  })

  it('keeps the years when opening the next rank down, a species and a hexagon, and Back brings them back',
    async () => {
      await openAt(`${TURDIDAE_SEARCH}&year_from=2000&year_to=2020`)
      fireEvent.click(screen.getByRole('button', { name: 'Open the genus Turdus' }))
      await act(async () => {})
      expect(window.location.search).toBe(`${TURDUS_SEARCH}&year_from=2000&year_to=2020`)
      expect(cardYears()).toEqual({ from: 2000, to: 2020 })
      clickHexagon(CELL_A)
      await act(async () => {})
      expect(window.location.search).toBe(`${TURDUS_SEARCH}&cell=${CELL_A}&year_from=2000&year_to=2020`)
      await closeWithX(`${TURDUS_SEARCH}&year_from=2000&year_to=2020`)
      fireEvent.click(screen.getByRole('button', { name: 'Open the robin from the rank' }))
      await act(async () => {})
      expect(window.location.search).toBe('?species=59PMB&year_from=2000&year_to=2020')
      expect(seen.map.years).toEqual({ from: 2000, to: 2020 })
      await pressBack(`${TURDUS_SEARCH}&year_from=2000&year_to=2020`)
      expect(cardYears()).toEqual({ from: 2000, to: 2020 })
      await pressBack(`${TURDIDAE_SEARCH}&year_from=2000&year_to=2020`)
      expect(cardYears()).toEqual({ from: 2000, to: 2020 })
    })

  it('keeps the years when the rank closes', async () => {
    await openAt(`${TURDUS_SEARCH}&year_from=2000`)
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await act(async () => {})
    expect(window.location.search).toBe('?year_from=2000')
    expect(seen.map.years).toEqual({ from: 2000, to: null })
    expect(seen.map.rank).toBeNull()
  })
})

describe('an area of interest', () => {
  // A box around Kamloops (45 hexagons at resolution 6, the size Auto picks
  // for it, as for the others below; 249 at 7), one near Prince
  // George (fully outside it), one over the east part of the Kamloops box,
  // and a box far too small to ask about; the addresses of areas of them.
  const KAMLOOPS = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
  const PRINCE_GEORGE = { west: -123.0, south: 53.8, east: -122.5, north: 54.0 }
  const OVERLAPPING = { west: -120.3, south: 50.7, east: -119.8, north: 50.95 }
  const TINY = { west: -120.3, south: 50.6, east: -120.29, north: 50.61 }
  const KAMLOOPS_TEXT = 'b:-120.6,50.55,-120.1,50.8'
  const PRINCE_GEORGE_TEXT = 'b:-123,53.8,-122.5,54'
  // Areas drawn or pasted are at Auto: no area_res in the address.
  const KAMLOOPS_SEARCH = `?area=${KAMLOOPS_TEXT}`
  const BOTH_SEARCH = `?area=${KAMLOOPS_TEXT};${PRINCE_GEORGE_TEXT}`
  const PRINCE_GEORGE_SEARCH = `?area=${PRINCE_GEORGE_TEXT}`
  // How many hexagons the Kamloops box touches at the size Auto picks.
  const KAMLOOPS_CELLS = boxCells(KAMLOOPS, 6).length

  // Draws a box on the map, at a resolution.
  function drawBox(box, res = 6) {
    act(() => seen.map.onAreaDrawn(box, res))
  }

  // Draws a box and waits for the address to be expectedSearch.
  async function drawBoxAndWait(box, expectedSearch, res = 6) {
    drawBox(box, res)
    await waitFor(() => expect(window.location.search).toBe(expectedSearch))
    await act(async () => {})
  }

  // The area panel's line when it is on screen, else null (closed, or
  // covered by a hexagon's panel).
  function areaText() {
    const holder = document.querySelector('.area-holder')
    return holder && !holder.hidden ? holder.querySelector('.stand-in-area p').textContent : null
  }

  // The area text of the area the map shows, or null for none.
  function mapAreaText() {
    return seen.map.selectedArea ? areaTextOfShapes(seen.map.selectedArea.shapes) : null
  }

  // Clicks the area panel's x and lets the page settle.
  async function closeAreaPanel() {
    fireEvent.click(screen.getByRole('button', { name: 'Close the area' }))
    await act(async () => {})
  }

  // Presses one of the map's area buttons ("Clear area", "Show area", the
  // panel's "Back", or a shape's x with its place) and waits for the address.
  async function pressMapButton(name, expectedSearch, place = null) {
    act(() => {
      if (name === 'Clear area') {
        seen.map.onClearArea()
      } else if (name === 'Show area') {
        seen.map.onShowArea()
      } else if (name === 'Back') {
        seen.map.onRemoveLastShape()
      } else {
        seen.map.onRemoveShape(place)
      }
    })
    await waitFor(() => expect(window.location.search).toBe(expectedSearch))
    await act(async () => {})
  }

  it('starts the area with the first box as a new address entry, with its panel and its hexagons on the map', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
    expect(areaText()).toBe(`Area ${KAMLOOPS_TEXT} at 6 (${KAMLOOPS_CELLS} hexagons) on the overview`)
    expect(seen.map.selectedArea.cells).toHaveLength(KAMLOOPS_CELLS)
    expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([6, false])
    expect(seen.map.onClearArea).toBeTypeOf('function')
    expect(seen.map.onShowArea).toBeNull()
  })

  it('adds each new box to the area as a new entry, which Back undoes', async () => {
    await openAfter('?species=59PMB', '')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    expect(calls()).toEqual({ added: 2, replaced: 0 })
    expect(history.goes).not.toHaveBeenCalled()
    expect(window.history.state).toEqual({ areaDepth: 1, areaBefore: '' })
    expect(mapAreaText()).toBe(`${KAMLOOPS_TEXT};${PRINCE_GEORGE_TEXT}`)
    expect(areaText()).toContain(`Area ${KAMLOOPS_TEXT};${PRINCE_GEORGE_TEXT} at 6`)
    const both = [boxShapeOf(KAMLOOPS), boxShapeOf(PRINCE_GEORGE)]
    expect(seen.map.selectedArea.cells).toEqual(shapesCells(both, 6).cells)
    // Back: the area with only the first box, then before it.
    await pressBack(KAMLOOPS_SEARCH)
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    await pressBack('')
    expect(seen.map.selectedArea).toBeNull()
    await pressBack('?species=59PMB')
  })

  it("counts an overlapping box's hexagons once, whatever the map's zoom", async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH, 4)
    // Drawn while the map shows another size: the zoom plays no part.
    await drawBoxAndWait(OVERLAPPING, `?area=${KAMLOOPS_TEXT};b:-120.3,50.7,-119.8,50.95`, 5)
    const joined = shapesCells([boxShapeOf(KAMLOOPS), boxShapeOf(OVERLAPPING)], 6).cells
    expect(seen.map.selectedArea.cells).toEqual(joined)
    expect(joined.length).toBeLessThan(KAMLOOPS_CELLS + boxCells(OVERLAPPING, 6).length)
  })

  it('keeps the area when a new box is refused, and says why in the API\'s words', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    const before = calls()
    drawBox(TINY, 6)
    await act(async () => {})
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    expect(calls()).toEqual(before)
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    expect(seen.map.areaMessage).toMatch(
      /^Shape 2 \(a box\) is about .* km², smaller than the minimum of 20\.6 km² for hexagons of about 5\.2 km² \(4 times their average area\)\. Draw a larger box\.$/)
    // A box that is fine after it is added and clears the message.
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    expect(seen.map.areaMessage).toBeNull()
  })

  it('removes a shape with its x as a new entry, which Back undoes', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    await pressMapButton('x', PRINCE_GEORGE_SEARCH, 0)
    expect(mapAreaText()).toBe(PRINCE_GEORGE_TEXT)
    expect(calls()).toEqual({ added: 3, replaced: 0 })
    expect(window.history.state).toEqual({ areaDepth: 2, areaBefore: '' })
    await pressBack(BOTH_SEARCH)
    expect(mapAreaText()).toBe(`${KAMLOOPS_TEXT};${PRINCE_GEORGE_TEXT}`)
  })

  it('removes the last shape with Back as a new entry, which the browser\'s Back undoes', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    await pressMapButton('Back', KAMLOOPS_SEARCH)
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    expect(calls()).toEqual({ added: 3, replaced: 0 })
    await pressBack(BOTH_SEARCH)
    expect(mapAreaText()).toBe(`${KAMLOOPS_TEXT};${PRINCE_GEORGE_TEXT}`)
  })

  it('leaves no area when the only shape is removed, which Back undoes', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await pressMapButton('Back', '')
    expect(seen.map.selectedArea).toBeNull()
    expect(areaText()).toBeNull()
    expect(window.history.state).toBeNull()
    await pressBack(KAMLOOPS_SEARCH)
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    // A box drawn after the area was emptied starts a new area.
    await pressMapButton('x', '', 0)
    await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
    expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
  })

  it('clears an area of several shapes with Clear area, never letting any of it come back', async () => {
    await openAfter('?species=59PMB', '')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    await pressMapButton('Back', KAMLOOPS_SEARCH)
    await pressMapButton('Clear area', '')
    // Back past the three entries holding the area, to the one before it.
    expect(history.goes).toHaveBeenCalledWith(-3)
    expect(seen.map.selectedArea).toBeNull()
    expect(areaText()).toBeNull()
    // Back then goes to what was there before the first box.
    await pressBack('?species=59PMB')
    expect(seen.map.selectedArea).toBeNull()
  })

  it('opens an old link (area=west,south,east,north&area_res) as one box at its size, kept as picked', async () => {
    await openAt('?area=-120.6,50.55,-120.1,50.8&area_res=6')
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    expect(areaText()).toBe(`Area ${KAMLOOPS_TEXT} at 6 (45 hexagons) on the overview`)
    expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([6, true])
    await drawBoxAndWait(PRINCE_GEORGE, `${BOTH_SEARCH}&area_res=6`)
    expect(window.history.state).toEqual({ areaDepth: 1 })
  })

  it('opens a clicked hexagon on top of the area panel, keeping the area; its x and Back return to the area', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(calls()).toEqual({ added: 2, replaced: 0 })
    expect(window.history.state).toEqual({ ...PANEL_FROM_CLICK, areaDepth: 1, areaBefore: '' })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(areaText()).toBeNull()
    expect(document.querySelector('.area-holder').hidden).toBe(true)
    expect(seen.map.selectedArea.cells).toHaveLength(KAMLOOPS_CELLS)
    expect(seen.map.selectedCell).toBe(CELL_A)
    // Another hexagon replaces the entry, still over the area.
    clickHexagon(CELL_B)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_B}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(calls()).toEqual({ added: 2, replaced: 1 })
    await closeWithX(KAMLOOPS_SEARCH)
    expect(history.backs).toHaveBeenCalledTimes(1)
    expect(panelText()).toBeNull()
    expect(areaText()).not.toBeNull()
    clickHexagon(CELL_A)
    await act(async () => {})
    await pressBack(KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
  })

  it('closes an open hexagon panel when a box is added, as a new entry Back undoes', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    expect(panelText()).toBeNull()
    expect(seen.map.selectedCell).toBeNull()
    expect(areaText()).not.toBeNull()
    await pressBack(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
  })

  it("closes only the panel with the area panel's x, keeping the area; Show area opens it again", async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await closeAreaPanel()
    expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_panel=closed`)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(history.backs).not.toHaveBeenCalled()
    expect(areaText()).toBeNull()
    expect(seen.map.selectedArea.cells).toHaveLength(KAMLOOPS_CELLS)
    expect(seen.map.onShowArea).toBeTypeOf('function')
    await pressMapButton('Show area', KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
    expect(seen.map.onShowArea).toBeNull()
    expect(calls()).toEqual({ added: 1, replaced: 2 })
  })

  it('clears the area with Clear area: like Back for the box drawn in this visit', async () => {
    await openAfter('?species=59PMB', '')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await pressMapButton('Clear area', '')
    expect(history.goes).toHaveBeenCalledWith(-1)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(areaText()).toBeNull()
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.onClearArea).toBeNull()
  })

  it('clears a shared area in place, and keeps a hexagon panel open on top of it', async () => {
    await openAt(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}&area_q=bee`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.selectedArea.cells).toHaveLength(KAMLOOPS_CELLS)
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    expect(history.backs).not.toHaveBeenCalled()
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.selectedArea).toBeNull()
  })

  it('keeps a cleared area gone when the hexagon panel opened over it is closed with its x', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    await drawBoxAndWait(PRINCE_GEORGE, BOTH_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}&${BOTH_SEARCH.slice(1)}`)
    // Clear area: both boxes go, the hexagon's panel stays.
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    expect(history.goes).toHaveBeenCalledWith(-3)
    expect(window.history.state).toEqual(PANEL_FROM_CLICK)
    expect(seen.map.selectedArea).toBeNull()
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    // The panel's x: the plain overview, with no area and no hexagon.
    await closeWithX('')
    expect(panelText()).toBeNull()
    expect(areaText()).toBeNull()
    expect(document.querySelector('.area-holder')).toBeNull()
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.selectedCell).toBeNull()
  })

  it('keeps a cleared area gone when Back is pressed after Clear area', async () => {
    await openAfter('?species=59PMB', '')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    // Back closes the hexagon's panel, as its x does, with no area.
    await pressBack('')
    expect(panelText()).toBeNull()
    expect(areaText()).toBeNull()
    expect(seen.map.selectedArea).toBeNull()
    // Back once more returns to what was there before the box was drawn.
    await pressBack('?species=59PMB')
    expect(seen.map.selectedArea).toBeNull()
  })

  it('keeps a cleared area gone after a species was opened from it and Back returned to it', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the area' }))
    await act(async () => {})
    await pressBack(KAMLOOPS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    await closeWithX('')
    expect(seen.map.selectedArea).toBeNull()
    expect(areaText()).toBeNull()
  })

  it('keeps a shared area gone once cleared, after a hexagon was clicked over it', async () => {
    await openAt(KAMLOOPS_SEARCH)
    clickHexagon(CELL_A)
    await act(async () => {})
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    expect(history.goes).toHaveBeenCalledWith(-1)
    // The shared link's own entry now holds the hexagon without the area,
    // so the x closes the panel in place.
    expect(window.history.state).toBeNull()
    await closeWithX('')
    expect(history.backs).not.toHaveBeenCalled()
    expect(seen.map.selectedArea).toBeNull()
    expect(areaText()).toBeNull()
  })

  it('keeps a cleared area gone when a rank was opened over it, and closing the rank goes back', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Pick the genus Turdus' }))
    await act(async () => {})
    expect(window.location.search).toBe(`${TURDUS_SEARCH}&${KAMLOOPS_SEARCH.slice(1)}`)
    await pressMapButton('Clear area', TURDUS_SEARCH)
    expect(window.history.state).toEqual({ rankOpenedFrom: '' })
    fireEvent.click(screen.getByRole('button', { name: 'Close the rank' }))
    await waitFor(() => expect(window.location.search).toBe(''))
    await act(async () => {})
    expect(history.backs).toHaveBeenCalledTimes(1)
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.rank).toBeNull()
  })

  it('clears a box drawn on a rank map like Back, returning to the rank alone', async () => {
    await openAfter('', TURDUS_SEARCH)
    await drawBoxAndWait(KAMLOOPS, `${TURDUS_SEARCH}&${KAMLOOPS_SEARCH.slice(1)}`)
    await pressMapButton('Clear area', TURDUS_SEARCH)
    expect(history.goes).toHaveBeenCalledWith(-1)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(seen.map.selectedArea).toBeNull()
  })

  it('refuses a box too small even at the smallest hexagons, whatever the zoom, and says why', async () => {
    await openAt('')
    drawBox(TINY, 4)
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(calls()).toEqual({ added: 0, replaced: 0 })
    expect(seen.map.areaMessage).toMatch(/^The box is about .* km², smaller than the minimum of 20\.6 km² for hexagons of about 5\.2 km²/)
    expect(seen.map.areaMessage).not.toContain('choose')
    act(() => seen.map.onDismissAreaMessage())
    expect(seen.map.areaMessage).toBeNull()
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    expect(seen.map.areaMessage).toBeNull()
  })

  describe('the hexagon size', () => {
    // Most of BC: over 200,000 hexagons at the smallest size, 70,747 at the next.
    const BC = { west: -139.1, south: 48.2, east: -114, north: 60 }
    const BC_TEXT = 'b:-139.1,48.2,-114,60'

    // Picks a size in the panel (a number, or null for Auto).
    function pickSize(res) {
      act(() => seen.map.onAreaSizeChange(res))
    }

    it('lets Auto choose again when shapes are added or removed', async () => {
      await openAt('')
      await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
      expect(seen.map.selectedArea.res).toBe(6)
      // Most of BC still fits at about 7 km: the size stays.
      await drawBoxAndWait(BC, `?area=${KAMLOOPS_TEXT};${BC_TEXT}`)
      expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([6, false])
      expect(seen.map.selectedArea.cells).toEqual(shapesCells([boxShapeOf(KAMLOOPS), boxShapeOf(BC)], 6).cells)
      await pressMapButton('Back', KAMLOOPS_SEARCH)
      // A box of about 75 km², too small for about 7 km: the whole area
      // moves down to about 3 km.
      const small = { west: -120.3, south: 50.6, east: -120.18, north: 50.68 }
      await drawBoxAndWait(small, `?area=${KAMLOOPS_TEXT};b:-120.3,50.6,-120.18,50.68`)
      expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([7, false])
      expect(seen.map.selectedArea.cells).toEqual(shapesCells([boxShapeOf(KAMLOOPS), boxShapeOf(small)], 7).cells)
      // Without it, back to about 7 km.
      await pressMapButton('Back', KAMLOOPS_SEARCH)
      expect(seen.map.selectedArea.res).toBe(6)
    })

    it('keeps a size picked as one history step, until Auto is chosen again or the area cleared', async () => {
      await openAfter('?species=59PMB', '')
      await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
      pickSize(6)
      await waitFor(() => expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=6`))
      await act(async () => {})
      expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([6, true])
      expect(seen.map.selectedArea.cells).toHaveLength(45)
      expect(calls()).toEqual({ added: 2, replaced: 0 })
      // A box added keeps the size picked.
      await drawBoxAndWait(PRINCE_GEORGE, `${BOTH_SEARCH}&area_res=6`)
      expect(seen.map.selectedArea.res).toBe(6)
      // The browser's Back undoes the box, then the size.
      await pressBack(`${KAMLOOPS_SEARCH}&area_res=6`)
      await pressBack(KAMLOOPS_SEARCH)
      expect([seen.map.selectedArea.res, seen.map.selectedArea.picked]).toEqual([6, false])
      // Picked again (about 3 km, smaller than Auto's choice), then Auto again.
      pickSize(7)
      await waitFor(() => expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=7`))
      pickSize(null)
      await waitFor(() => expect(window.location.search).toBe(KAMLOOPS_SEARCH))
      await act(async () => {})
      // After Clear area, the next box is at Auto again.
      pickSize(6)
      await waitFor(() => expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=6`))
      await pressMapButton('Clear area', '')
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
    })

    it('refuses a size, or a shape, that does not pass at the size picked, with a hint on the page', async () => {
      await openAt('')
      await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
      const before = calls()
      // Kamloops (about 979 km²) is under 4 hexagons of about 1,770 km².
      pickSize(4)
      await act(async () => {})
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
      expect(calls()).toEqual(before)
      expect(seen.map.areaMessage).toBe('The box is about 979.4 km², smaller than the minimum of 7,081.4 km² for '
        + 'hexagons of about 1,770.3 km² (4 times their average area). Draw a larger box. Or choose smaller hexagons.')
      // A size that passes, then a box too small for it.
      pickSize(6)
      await waitFor(() => expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=6`))
      await act(async () => {})
      expect(seen.map.areaMessage).toBeNull()
      drawBox(TINY)
      await act(async () => {})
      expect(seen.map.areaMessage).toMatch(/^Shape 2 \(a box\) is about .* smaller than the minimum of 144\.5 km² .*Draw a larger box\. Or choose smaller hexagons\.$/)
      // A box too big at the smallest size: the API's own words already say
      // to use larger hexagons, so no hint is added.
      pickSize(7)
      await waitFor(() => expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=7`))
      await act(async () => {})
      drawBox(BC)
      await act(async () => {})
      expect(seen.map.areaMessage).toMatch(/^Shape 2 \(a box\) touches about 4\d\d,\d\d\d hexagons of about 5\.2 km², more than the 200,000 an area search can count\. Draw a smaller box or use larger hexagons\.$/)
      expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_res=7`)
    })
  })

  it("keeps a rank map's rank, listing only its species, over the rank card", async () => {
    await openAt(TURDUS_SEARCH)
    await drawBoxAndWait(KAMLOOPS, `${TURDUS_SEARCH}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(areaText()).toContain('on the map of genus Turdus')
    expect(document.querySelector('.card-holder:not(.area-holder)').hidden).toBe(true)
    // With the area panel closed, the rank card shows again under the area.
    await closeAreaPanel()
    expect(document.querySelector('.card-holder:not(.area-holder)').hidden).toBe(false)
    expect(seen.map.selectedArea).not.toBeNull()
  })

  it('keeps the area while a species map is open, without showing it, and brings it back with the overview', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: 'Open the bumble bee from the area' }))
    await act(async () => {})
    expect(window.location.search).toBe(`?species=CCCC&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(areaText()).toBeNull()
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.onClearArea).toBeNull()
    expect(cardShown()).toBe(true)
    expect(screen.getByRole('button', { name: "Back to this area's species" })).toBeTruthy()
    await pressBack(KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
    // Closing a card picked in the search box returns to the area too.
    fireEvent.click(screen.getByRole('button', { name: 'Pick the robin' }))
    await act(async () => {})
    expect(window.location.search).toBe(`?species=59PMB&${KAMLOOPS_SEARCH.slice(1)}`)
    fireEvent.click(screen.getByRole('button', { name: 'Close the card' }))
    await act(async () => {})
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
  })

  describe('a paste', () => {
    // Two polygons near Kamloops and Prince George, as WKT, and the area
    // text they become.
    const TWO_POLYGONS = 'MULTIPOLYGON(((-121 50, -119.2 50, -119 51, -120.4 51.2, -121 50)), '
      + '((-123.5 53.5, -122 53.5, -122 54.5, -123.5 54.5)))'
    const TWO_TEXT = 'p:-121,50,-119.2,50,-119,51,-120.4,51.2;p:-123.5,53.5,-122,53.5,-122,54.5,-123.5,54.5'

    // Pastes text in the panel, and returns what the map is told (null when
    // the shapes were added, else why not).
    function paste(text) {
      let problem
      act(() => {
        problem = seen.map.onAreaPasted(text)
      })
      return problem
    }

    it('adds every pasted shape as one entry, at Auto; Back takes the whole paste away', async () => {
      await openAt('')
      expect(paste(TWO_POLYGONS)).toBeNull()
      await waitFor(() => expect(window.location.search).toBe(`?area=${TWO_TEXT}`))
      await act(async () => {})
      expect(calls()).toEqual({ added: 1, replaced: 0 })
      expect(seen.map.selectedArea.shapes.map((shape) => shape.isBox)).toEqual([false, false])
      expect(areaText()).toBe(`Area ${TWO_TEXT} at 6 (${seen.map.selectedArea.cells.length} hexagons) on the overview`)
      await pressBack('')
      expect(seen.map.selectedArea).toBeNull()
    })

    it("adds to an area after its shapes, and the panel's Back removes one shape", async () => {
      await openAt('')
      await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
      expect(paste(TWO_POLYGONS)).toBeNull()
      const all = `?area=${KAMLOOPS_TEXT};${TWO_TEXT}`
      await waitFor(() => expect(window.location.search).toBe(all))
      await act(async () => {})
      expect(calls()).toEqual({ added: 2, replaced: 0 })
      await pressMapButton('Back', `?area=${KAMLOOPS_TEXT};p:-121,50,-119.2,50,-119,51,-120.4,51.2`)
      await pressBack(all)
      // The browser's Back after the paste returns to the box alone.
      await pressBack(KAMLOOPS_SEARCH)
      expect(mapAreaText()).toBe(KAMLOOPS_TEXT)
    })

    it('adds nothing when any part is refused, and says why', async () => {
      await openAt('')
      await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
      const before = calls()
      const withHole = 'MULTIPOLYGON(((-121 50, -119.2 50, -119 51, -121 50)), ((-123.5 53.5, -122 53.5, -122 54.5, '
        + '-123.5 53.5), (-123 53.8, -122.5 53.8, -122.5 54, -123 53.8)))'
      expect(paste(withHole)).toBe('Shape 3 (a polygon) has a hole; holes are not supported yet. Paste its outer '
        + 'edge only.')
      await act(async () => {})
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
      expect(calls()).toEqual(before)
      expect(paste('POINT(-121 50)')).toBe('Only polygons can be added; this is a POINT.')
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    })
  })

  it('keeps the list options of the area when its list changes, in place', async () => {
    await openAt('')
    await drawBoxAndWait(KAMLOOPS, KAMLOOPS_SEARCH)
    fireEvent.click(screen.getByRole('button', { name: "Filter the area's list" }))
    await act(async () => {})
    expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}`
      + '&area_q=bee&area_sort=english&area_status=red&area_status=none&area_page=2')
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
  })
})
