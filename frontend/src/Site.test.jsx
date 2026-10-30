import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The two pages swapped for stand-ins, so only the top bar and the switching
// between pages are tested here.
vi.mock('./App', () => ({
  // A stand-in for the species map page that shows the address it was given.
  default: function App({ search }) {
    return <p>The species map{search && ` for ${search}`}</p>
  },
}))
vi.mock('./DatasetsPage', () => ({
  // A stand-in for the Datasets page that shows the address it was given.
  default: function DatasetsPage({ search }) {
    return <p>Datasets for {search}</p>
  },
}))

// Imported after the stand-ins above, which vitest puts in place first.
import Site from './Site'

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('links between the pages', () => {
  it('gives the species map the address, so a card can be opened from it and the back button returns to it', () => {
    window.history.replaceState(null, '', '/?species=49JFH')
    render(<Site />)
    expect(screen.getByText('The species map for ?species=49JFH')).toBeTruthy()
    act(() => {
      window.history.pushState(null, '', '/?view=datasets&species=49JFH')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByText('Datasets for ?view=datasets&species=49JFH')).toBeTruthy()
    act(() => {
      window.history.replaceState(null, '', '/?species=49JFH')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByText('The species map for ?species=49JFH')).toBeTruthy()
  })
})

describe('the top bar', () => {
  it('opens on the species map, unchanged, and switches to the datasets and back', () => {
    render(<Site />)
    expect(screen.getByText('The species map')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Species map' }).getAttribute('aria-current')).toBe('page')

    fireEvent.click(screen.getByRole('link', { name: 'Datasets' }))
    expect(screen.getByText('Datasets for ?view=datasets')).toBeTruthy()
    expect(window.location.search).toBe('?view=datasets')

    fireEvent.click(screen.getByRole('link', { name: 'Species map' }))
    expect(screen.getByText('The species map')).toBeTruthy()
    expect(window.location.search).toBe('')
  })

  it('follows the back button', () => {
    window.history.replaceState(null, '', '/?view=datasets&q=eagle')
    render(<Site />)
    expect(screen.getByText('Datasets for ?view=datasets&q=eagle')).toBeTruthy()
    act(() => {
      window.history.replaceState(null, '', '/')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByText('The species map')).toBeTruthy()
  })
})
