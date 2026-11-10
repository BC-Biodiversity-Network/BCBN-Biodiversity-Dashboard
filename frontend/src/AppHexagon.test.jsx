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
  // A stand-in area panel: its box, resolution and map, its x, a species in
  // its list, and a change to its list.
  default: function AreaPanel({ area, cellCount, rank, list, onListChange, onClose, onOpenSpecies }) {
    const map = rank ? `the map of ${rank.rank} ${rank.name}` : 'the overview'
    return (
      <div className="stand-in-area">
        <p>Area {JSON.stringify(area.box)} at {area.res} ({cellCount} hexagons) on {map}</p>
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

describe('a box drawn on the map', () => {
  // A box around Kamloops (45 hexagons at resolution 6), its address, and a
  // box far too small to ask about.
  const KAMLOOPS = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
  const KAMLOOPS_SEARCH = '?area=-120.6,50.55,-120.1,50.8&area_res=6'
  const TINY = { west: -120.3, south: 50.6, east: -120.29, north: 50.61 }

  // Draws a box on the map, at a resolution.
  function drawBox(box, res = 6) {
    act(() => seen.map.onAreaDrawn(box, res))
  }

  // Draws a box while an area exists and waits for the address to be
  // expectedSearch (the old area is first taken out of the history, which
  // the page follows a moment later).
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

  // Clicks the area panel's x and lets the page settle.
  async function closeAreaPanel() {
    fireEvent.click(screen.getByRole('button', { name: 'Close the area' }))
    await act(async () => {})
  }

  // Presses the map's "Clear area" (or "Show area") and waits for the address.
  async function pressMapButton(name, expectedSearch) {
    act(() => (name === 'Clear area' ? seen.map.onClearArea() : seen.map.onShowArea()))
    await waitFor(() => expect(window.location.search).toBe(expectedSearch))
    await act(async () => {})
  }

  it('selects the area as a new address entry, with its panel and its hexagons on the map', async () => {
    await openAt('')
    drawBox(KAMLOOPS)
    await act(async () => {})
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
    expect(areaText()).toBe(`Area ${JSON.stringify(KAMLOOPS)} at 6 (45 hexagons) on the overview`)
    expect(seen.map.selectedArea.cells).toHaveLength(45)
    expect(seen.map.onClearArea).toBeTypeOf('function')
    expect(seen.map.onShowArea).toBeNull()
  })

  it('replaces the area when another box is drawn, and the entry when its list changes', async () => {
    await openAt('')
    drawBox(KAMLOOPS)
    await act(async () => {})
    await drawBoxAndWait({ ...KAMLOOPS, east: -120.0 }, '?area=-120.6,50.55,-120,50.8&area_res=7', 7)
    fireEvent.click(screen.getByRole('button', { name: "Filter the area's list" }))
    await act(async () => {})
    expect(window.location.search).toBe('?area=-120.6,50.55,-120,50.8&area_res=7'
      + '&area_q=bee&area_sort=english&area_status=red&area_status=none&area_page=2')
    expect(calls()).toEqual({ added: 2, replaced: 1 })
    expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
  })

  describe('a second box', () => {
    // A box near Prince George, fully outside the box around Kamloops; one
    // over the east part of the Kamloops box and past it; their addresses.
    const PRINCE_GEORGE = { west: -123.0, south: 53.8, east: -122.5, north: 54.0 }
    const PRINCE_GEORGE_SEARCH = '?area=-123,53.8,-122.5,54&area_res=6'
    const OVERLAPPING = { west: -120.3, south: 50.7, east: -119.8, north: 50.95 }
    const OVERLAPPING_SEARCH = '?area=-120.3,50.7,-119.8,50.95&area_res=6'

    // Checks that the area shown, on the map and in its panel, is box.
    function expectArea(box) {
      expect(seen.map.selectedArea.box).toEqual(box)
      expect(areaText()).toContain(`Area ${JSON.stringify(box)} at 6`)
    }

    it('replaces the area when drawn fully outside it, and Back returns to before the first box', async () => {
      await openAfter('?species=59PMB', '')
      drawBox(KAMLOOPS)
      await act(async () => {})
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
      expectArea(KAMLOOPS)
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      expectArea(PRINCE_GEORGE)
      expect(window.history.state).toEqual({ areaDepth: 0, areaBefore: '' })
      expect(history.goes).toHaveBeenCalledWith(-1)
      // Back: what was there before the first box, never the first box.
      await pressBack('')
      expect(seen.map.selectedArea).toBeNull()
      expect(areaText()).toBeNull()
      await pressBack('?species=59PMB')
    })

    it('replaces the area when drawn over part of it', async () => {
      await openAt('')
      drawBox(KAMLOOPS)
      await act(async () => {})
      await drawBoxAndWait(OVERLAPPING, OVERLAPPING_SEARCH)
      expectArea(OVERLAPPING)
      expect(seen.map.selectedArea.cells).toHaveLength(boxCells(OVERLAPPING, 6).length)
    })

    it('keeps the area when the new box is too small, and says why', async () => {
      await openAt('')
      drawBox(KAMLOOPS)
      await act(async () => {})
      const before = calls()
      drawBox(TINY, 6)
      await act(async () => {})
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
      expect(calls()).toEqual(before)
      expect(history.goes).not.toHaveBeenCalled()
      expectArea(KAMLOOPS)
      expect(seen.map.areaMessage).toMatch(/^This box is about .* km², smaller than the minimum/)
      // A valid box after it replaces the area and clears the message.
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      expectArea(PRINCE_GEORGE)
      expect(seen.map.areaMessage).toBeNull()
    })

    it('leaves no entry holding the old box after a hexagon was opened over it', async () => {
      await openAt('')
      drawBox(KAMLOOPS)
      await act(async () => {})
      clickHexagon(CELL_A)
      await act(async () => {})
      expect(window.location.search).toBe(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}`)
      // The new box closes the hexagon's panel and replaces the area.
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      expect(history.goes).toHaveBeenCalledWith(-2)
      expect(panelText()).toBeNull()
      expectArea(PRINCE_GEORGE)
      // A hexagon over the new area, then its x: back to the new area.
      clickHexagon(CELL_B)
      await act(async () => {})
      await closeWithX(PRINCE_GEORGE_SEARCH)
      expectArea(PRINCE_GEORGE)
      // Back: before the first box, not the old box with its panel.
      await pressBack('')
      expect(seen.map.selectedArea).toBeNull()
      expect(areaText()).toBeNull()
    })

    it('after a hexagon, box A, a hexagon, box B: Back shows the first hexagon, outlined on the map', async () => {
      await openAt('')
      clickHexagon(CELL_A)
      await act(async () => {})
      expect(window.location.search).toBe(`?cell=${CELL_A}`)
      drawBox(KAMLOOPS)
      await act(async () => {})
      expect(window.location.search).toBe(KAMLOOPS_SEARCH)
      clickHexagon(CELL_B)
      await act(async () => {})
      expect(window.location.search).toBe(`?cell=${CELL_B}&${KAMLOOPS_SEARCH.slice(1)}`)
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      expect(seen.map.selectedCell).toBeNull()
      expectArea(PRINCE_GEORGE)
      await pressBack(`?cell=${CELL_A}`)
      // The panel and the map name the same hexagon, with no box.
      expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
      expect(seen.map.selectedCell).toBe(CELL_A)
      expect(seen.map.selectedArea).toBeNull()
      expect(areaText()).toBeNull()
      // Its x then closes it, back to the plain map.
      await closeWithX('')
      expect(seen.map.selectedCell).toBeNull()
      expect(panelText()).toBeNull()
    })

    it('after box A, a hexagon, box B: Back shows the plain map, with no panel and no hexagon', async () => {
      await openAfter('?species=59PMB', '')
      drawBox(KAMLOOPS)
      await act(async () => {})
      clickHexagon(CELL_B)
      await act(async () => {})
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      await pressBack('')
      expect(panelText()).toBeNull()
      expect(seen.map.selectedCell).toBeNull()
      expect(seen.map.selectedArea).toBeNull()
      expect(areaText()).toBeNull()
    })

    it("replaces a shared link's area in place", async () => {
      await openAt(KAMLOOPS_SEARCH)
      await drawBoxAndWait(PRINCE_GEORGE, PRINCE_GEORGE_SEARCH)
      expect(calls()).toEqual({ added: 0, replaced: 1 })
      expect(history.goes).not.toHaveBeenCalled()
      expect(window.history.state).toEqual({ areaDepth: 0 })
      expectArea(PRINCE_GEORGE)
    })
  })

  it('opens a clicked hexagon on top of the area panel, keeping the area; its x and Back return to the area', async () => {
    await openAt('')
    drawBox(KAMLOOPS)
    await act(async () => {})
    clickHexagon(CELL_A)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(calls()).toEqual({ added: 2, replaced: 0 })
    expect(window.history.state).toEqual({ ...PANEL_FROM_CLICK, areaDepth: 1, areaBefore: '' })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(areaText()).toBeNull()
    expect(document.querySelector('.area-holder').hidden).toBe(true)
    expect(seen.map.selectedArea.cells).toHaveLength(45)
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

  it("closes only the panel with the area panel's x, keeping the area; Show area opens it again", async () => {
    await openAt('')
    drawBox(KAMLOOPS)
    await act(async () => {})
    await closeAreaPanel()
    expect(window.location.search).toBe(`${KAMLOOPS_SEARCH}&area_panel=closed`)
    expect(calls()).toEqual({ added: 1, replaced: 1 })
    expect(history.backs).not.toHaveBeenCalled()
    expect(areaText()).toBeNull()
    expect(seen.map.selectedArea.cells).toHaveLength(45)
    expect(seen.map.onShowArea).toBeTypeOf('function')
    await pressMapButton('Show area', KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
    expect(seen.map.onShowArea).toBeNull()
    expect(calls()).toEqual({ added: 1, replaced: 2 })
  })

  it('clears the area with Clear area: like Back for the box drawn in this visit', async () => {
    await openAfter('?species=59PMB', '')
    drawBox(KAMLOOPS)
    await act(async () => {})
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
    expect(seen.map.selectedArea.cells).toHaveLength(45)
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    expect(history.backs).not.toHaveBeenCalled()
    expect(calls()).toEqual({ added: 0, replaced: 1 })
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.selectedArea).toBeNull()
  })

  it('keeps a cleared area gone when the hexagon panel opened over it is closed with its x', async () => {
    await openAt('')
    expect(window.location.search).toBe('')
    // 1. Draw a box: the area panel opens.
    drawBox(KAMLOOPS)
    await act(async () => {})
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    expect(areaText()).not.toBeNull()
    // 2. Click a hexagon inside the area: its panel opens on top.
    clickHexagon(CELL_A)
    await act(async () => {})
    expect(window.location.search).toBe(`?cell=${CELL_A}&${KAMLOOPS_SEARCH.slice(1)}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    // 3. Clear area: the box goes, the hexagon's panel stays.
    await pressMapButton('Clear area', `?cell=${CELL_A}`)
    expect(history.goes).toHaveBeenCalledWith(-2)
    expect(window.history.state).toEqual(PANEL_FROM_CLICK)
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.onClearArea).toBeNull()
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    // 4. The panel's x: the plain overview, with no area and no hexagon.
    await closeWithX('')
    expect(panelText()).toBeNull()
    expect(areaText()).toBeNull()
    expect(document.querySelector('.area-holder')).toBeNull()
    expect(seen.map.selectedArea).toBeNull()
    expect(seen.map.selectedCell).toBeNull()
  })

  it('keeps a cleared area gone when Back is pressed after Clear area', async () => {
    await openAfter('?species=59PMB', '')
    drawBox(KAMLOOPS)
    await act(async () => {})
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
    drawBox(KAMLOOPS)
    await act(async () => {})
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
    drawBox(KAMLOOPS)
    await act(async () => {})
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
    drawBox(KAMLOOPS)
    await act(async () => {})
    await pressMapButton('Clear area', TURDUS_SEARCH)
    expect(history.goes).toHaveBeenCalledWith(-1)
    expect(calls()).toEqual({ added: 1, replaced: 0 })
    expect(seen.map.selectedArea).toBeNull()
  })

  it('closes an open hexagon panel when the first box is drawn, as a new entry Back undoes', async () => {
    await openAt('')
    clickHexagon(CELL_A)
    await act(async () => {})
    drawBox(KAMLOOPS)
    await act(async () => {})
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
    expect(panelText()).toBeNull()
    expect(areaText()).not.toBeNull()
    expect(calls()).toEqual({ added: 2, replaced: 0 })
    await pressBack(`?cell=${CELL_A}`)
    expect(panelText()).toBe(`Panel of ${CELL_A} on the overview`)
    expect(seen.map.selectedArea).toBeNull()
  })

  it('does not ask about a box too small or too big: the map says why instead', async () => {
    await openAt('')
    drawBox(TINY, 4)
    await act(async () => {})
    expect(window.location.search).toBe('')
    expect(calls()).toEqual({ added: 0, replaced: 0 })
    expect(seen.map.areaMessage).toMatch(/^This box is about .* km², smaller than the minimum of 7,081\.4 km²/)
    drawBox({ west: -139.1, south: 48.2, east: -114, north: 60 }, 7)
    await act(async () => {})
    expect(seen.map.areaMessage).toMatch(/more than the 200,000 an area search can count/)
    act(() => seen.map.onDismissAreaMessage())
    expect(seen.map.areaMessage).toBeNull()
    drawBox(KAMLOOPS)
    await act(async () => {})
    expect(seen.map.areaMessage).toBeNull()
    expect(window.location.search).toBe(KAMLOOPS_SEARCH)
  })

  it("keeps a rank map's rank, listing only its species, over the rank card", async () => {
    await openAt(TURDUS_SEARCH)
    drawBox(KAMLOOPS)
    await act(async () => {})
    expect(window.location.search).toBe(`${TURDUS_SEARCH}&area=-120.6,50.55,-120.1,50.8&area_res=6`)
    expect(areaText()).toContain('on the map of genus Turdus')
    expect(document.querySelector('.card-holder:not(.area-holder)').hidden).toBe(true)
    // With the area panel closed, the rank card shows again under the area.
    await closeAreaPanel()
    expect(document.querySelector('.card-holder:not(.area-holder)').hidden).toBe(false)
    expect(seen.map.selectedArea).not.toBeNull()
  })

  it('keeps the area while a species map is open, without showing it, and brings it back with the overview', async () => {
    await openAt('')
    drawBox(KAMLOOPS)
    await act(async () => {})
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
})
