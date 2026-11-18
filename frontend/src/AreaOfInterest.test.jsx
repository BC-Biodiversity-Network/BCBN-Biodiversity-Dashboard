import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AreaOfInterest, { DRAW_HELP, DRAW_HINT, shapeCountText, shapeLabel } from './AreaOfInterest'
import { PASTE_EXAMPLE, PASTE_HELP } from './areaPaste'
import { boxShapeOf, readAreaText } from './areaShapes'

afterEach(cleanup)

// The panel with every handler a mock, and the props given.
function show(props = {}) {
  const handlers = {
    onOpenChange: vi.fn(), onDrawBox: vi.fn(), onRemoveShape: vi.fn(), onBack: vi.fn(), onClear: vi.fn(),
    onDismissMessage: vi.fn(),
  }
  render(<AreaOfInterest open shapes={[]} drawing={false} unavailable={false} message={null} {...handlers}
    {...props} />)
  return handlers
}

describe('the area of interest panel', () => {
  it('counts and names the shapes', () => {
    expect([0, 1, 3, 1200].map(shapeCountText)).toEqual(['No shapes yet', '1 shape', '3 shapes', '1,200 shapes'])
    const [box, polygon] = readAreaText('b:0,0,1,1;p:2,2,3,2,3,3,2.5,3.5')
    expect(shapeLabel(box, 0)).toBe('Shape 1: box')
    expect(shapeLabel(polygon, 1)).toBe('Shape 2: polygon (4 points)')
  })

  it('lists the shapes, each with an x, and hands on x, Back, Clear area and Box', () => {
    const shapes = [boxShapeOf({ west: 0, south: 0, east: 1, north: 1 }), boxShapeOf({ west: 2, south: 2, east: 3,
      north: 3 })]
    const handlers = show({ shapes })
    expect(screen.getByText('2 shapes')).toBeTruthy()
    expect(screen.getAllByRole('listitem').map((item) => item.querySelector('span').textContent))
      .toEqual(['Shape 1: box', 'Shape 2: box'])
    fireEvent.click(screen.getByRole('button', { name: 'Remove shape 1' }))
    expect(handlers.onRemoveShape).toHaveBeenCalledWith(0)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Box' }))
    expect([handlers.onBack, handlers.onClear, handlers.onDrawBox].map((mock) => mock.mock.calls.length))
      .toEqual([1, 1, 1])
    expect(screen.getByText(DRAW_HELP)).toBeTruthy()
  })

  it('has the Draw and Paste tabs, and Back and Clear area off with no shapes', () => {
    show()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Draw', 'Paste'])
    expect(screen.queryByRole('list')).toBeNull()
    expect(screen.getByRole('button', { name: 'Back' }).disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Clear area' }).disabled).toBe(true)
  })

  it('shows the hint while drawing, with Box pressed, and the message otherwise', () => {
    const handlers = show({ drawing: true, message: 'The box is about 1.0 km², too small.' })
    expect(screen.getByRole('status').textContent).toBe(DRAW_HINT)
    expect(screen.getByRole('button', { name: 'Box' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByRole('alert')).toBeNull()
    cleanup()
    show({ message: 'The box is about 1.0 km², too small.', ...handlers })
    expect(screen.getByRole('alert').textContent).toContain('The box is about 1.0 km², too small.')
  })

  it('opens and closes with its button and its Close', () => {
    const handlers = show({ open: false })
    expect(screen.queryByRole('region')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Create an area of interest' }))
    expect(handlers.onOpenChange).toHaveBeenCalledWith(true)
    cleanup()
    const again = show()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(again.onOpenChange).toHaveBeenCalledWith(false)
  })

  it('is greyed out, with no panel, on a single species map', () => {
    show({ unavailable: true })
    expect(screen.getByRole('button', { name: 'Create an area of interest' }).disabled).toBe(true)
    expect(screen.queryByRole('region')).toBeNull()
  })

  it('pastes in its Paste tab: Add hands on the text, which goes when added and stays with the reason when not', () => {
    const onPaste = vi.fn().mockReturnValueOnce('The polygon has a hole; holes are not supported yet.')
      .mockReturnValueOnce(null)
    show({ onPaste })
    fireEvent.click(screen.getByRole('tab', { name: 'Paste' }))
    expect(screen.getByRole('tab', { name: 'Paste' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.queryByRole('button', { name: 'Box' })).toBeNull()
    expect(screen.getByText(PASTE_HELP)).toBeTruthy()
    const box = screen.getByRole('textbox', { name: 'Shapes to paste, as WKT or GeoJSON' })
    fireEvent.change(box, { target: { value: 'POLYGON((0 0, 1 0, 1 1, 0 0), (0.1 0.1, 0.2 0.1, 0.2 0.2, 0.1 0.1))' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(onPaste).toHaveBeenLastCalledWith(box.value)
    expect(screen.getByRole('alert').textContent).toBe('The polygon has a hole; holes are not supported yet.')
    expect(box.value).toContain('POLYGON')
    // The example fills the box and clears the reason; Add then empties it.
    fireEvent.click(screen.getByRole('button', { name: 'Fill in an example' }))
    expect(box.value).toBe(PASTE_EXAMPLE)
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(onPaste).toHaveBeenLastCalledWith(PASTE_EXAMPLE)
    expect(box.value).toBe('')
  })

  it('stops drawing a box when the Paste tab is opened', () => {
    const handlers = show({ drawing: true })
    fireEvent.click(screen.getByRole('tab', { name: 'Paste' }))
    expect(handlers.onDrawBox).toHaveBeenCalledTimes(1)
  })
})
