import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The species map page with the real address handling (useLocationSearch on
// the test page's own history, so new entries, replaced entries and the back
// button behave as in a browser) and stand-ins for its parts: the map
// records its props, the search box picks the robin, the species card, the
// rank card and the hexagon panel are a line of text and buttons.
const seen = vi.hoisted(() => ({ map: null }))
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
  default: function HexagonPanel({ cell, species, rank, onClose, onOpenSpecies, onShowAllSpecies }) {
    const map = species ? `the map of ${species.key}` : rank ? `the map of ${rank.rank} ${rank.name}` : 'the overview'
    return (
      <div className="stand-in-panel">
        <p>Panel of {cell} on {map}</p>
        <button type="button" onClick={onClose}>Close the panel</button>
        <button type="button" onClick={() => onOpenSpecies('CCCC')}>Open the bumble bee from the list</button>
        <button type="button" onClick={onShowAllSpecies}>Show all species</button>
      </div>
    )
  },
}))
vi.mock('./RankCard', () => ({
  // A stand-in rank card: its rank, its x, the genus Turdus as the next
  // rank down, and the robin in its list.
  default: function RankCard({ rankRef, onClose, onOpenSpecies, onOpenRank }) {
    return (
      <div className="stand-in-rank-card">
        <p>Card of {rankRef.rank} {rankRef.name}</p>
        <button type="button" onClick={onClose}>Close the rank</button>
        <button type="button" onClick={() => onOpenRank(TURDUS)}>Open the genus Turdus</button>
        <button type="button" onClick={() => onOpenSpecies('59PMB')}>Open the robin from the rank</button>
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
import { useLocationSearch } from './route'

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
const history = { pushes: null, replaces: null, backs: null }

// Opens the page at a query string, as a fresh visit, and lets it settle.
async function openAt(query) {
  window.history.replaceState(null, '', `/${query}`)
  render(<Page />)
  await act(async () => {})
  history.pushes.mockClear()
  history.replaces.mockClear()
  history.backs.mockClear()
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
  const holder = document.querySelector('.card-holder')
  return Boolean(holder) && !holder.hidden
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
  history.pushes = vi.spyOn(window.history, 'pushState')
  history.replaces = vi.spyOn(window.history, 'replaceState')
  history.backs = vi.spyOn(window.history, 'back')
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
