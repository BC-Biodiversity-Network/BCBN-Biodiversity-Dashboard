import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The species map page with the real address handling (useLocationSearch on
// the test page's own history, so new entries, replaced entries and the back
// button behave as in a browser) and stand-ins for its parts: the map
// records its props, the search box picks the robin, the card and the
// hexagon panel are a line of text and buttons.
const seen = vi.hoisted(() => ({ map: null }))

vi.mock('./HexMap', () => ({
  // A stand-in map that only records its props.
  default: function HexMap(props) {
    seen.map = props
    return null
  },
}))
vi.mock('./SearchBox', () => ({
  // A stand-in search box: one button that picks the robin.
  default: function SearchBox({ onSelect }) {
    return <button type="button" onClick={() => onSelect({ specieskey: '59PMB', col_name: 'Turdus migratorius' })}>Pick the robin</button>
  },
}))
vi.mock('./SpeciesCard', () => ({
  // A stand-in card: its key and a button that closes it.
  default: function SpeciesCard({ requestedKey, onClose }) {
    return (
      <div className="stand-in-card">
        <p>Card of {requestedKey}</p>
        <button type="button" onClick={onClose}>Close the card</button>
      </div>
    )
  },
}))
vi.mock('./HexagonPanel', () => ({
  // A stand-in panel: which hexagon on which map, its x, and a species in
  // its list.
  default: function HexagonPanel({ cell, species, onClose, onOpenSpecies, onShowAllSpecies }) {
    return (
      <div className="stand-in-panel">
        <p>Panel of {cell} on {species ? `the map of ${species.key}` : 'the overview'}</p>
        <button type="button" onClick={onClose}>Close the panel</button>
        <button type="button" onClick={() => onOpenSpecies('CCCC')}>Open the bumble bee from the list</button>
        <button type="button" onClick={onShowAllSpecies}>Show all species</button>
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
