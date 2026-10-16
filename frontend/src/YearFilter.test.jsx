import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import YearFilter from './YearFilter'

const BOUNDS = { firstYear: 1784, lastYear: 2026 }

afterEach(cleanup)

// The two year boxes.
function boxes() {
  return [screen.getByRole('spinbutton', { name: 'First year' }), screen.getByRole('spinbutton', { name: 'Last year' })]
}

// Types a value into a box, as a person would (without leaving it).
function type(box, value) {
  fireEvent.change(box, { target: { value } })
}

// Presses Enter in a box.
function pressEnter(box) {
  fireEvent.keyDown(box, { key: 'Enter' })
}

describe('the year control', () => {
  it("shows all years at first, with the data's years as hints and the note on recent years", () => {
    render(<YearFilter years={null} bounds={BOUNDS} onChange={() => {}} />)
    const [from, to] = boxes()
    expect(from.value).toBe('')
    expect(from.placeholder).toBe('1784')
    expect(to.placeholder).toBe('2026')
    expect(screen.getByRole('button', { name: 'All years' }).disabled).toBe(true)
    expect(screen.getByText(/Records from 2025 and 2026 are still incomplete/)).toBeTruthy()
  })

  it('applies a typed range when Enter is pressed, not on every key', () => {
    const onChange = vi.fn()
    render(<YearFilter years={null} bounds={BOUNDS} onChange={onChange} />)
    const [from, to] = boxes()
    type(from, '2')
    type(from, '20')
    type(from, '2000')
    type(to, '2020')
    expect(onChange).not.toHaveBeenCalled()
    pressEnter(to)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith({ from: 2000, to: 2020 })
  })

  it('applies when the control is left, but not when moving between the boxes', () => {
    const onChange = vi.fn()
    render(<YearFilter years={null} bounds={BOUNDS} onChange={onChange} />)
    const [from, to] = boxes()
    type(from, '2000')
    fireEvent.blur(from, { relatedTarget: to })
    expect(onChange).not.toHaveBeenCalled()
    type(to, '2020')
    fireEvent.blur(to, { relatedTarget: document.body })
    expect(onChange).toHaveBeenCalledWith({ from: 2000, to: 2020 })
  })

  it('takes one end alone as an open range', () => {
    const onChange = vi.fn()
    render(<YearFilter years={null} bounds={BOUNDS} onChange={onChange} />)
    type(boxes()[0], '2015')
    pressEnter(boxes()[0])
    expect(onChange).toHaveBeenCalledWith({ from: 2015, to: null })
  })

  it('says what is wrong instead of applying a year outside the data or a backwards range', () => {
    const onChange = vi.fn()
    render(<YearFilter years={null} bounds={BOUNDS} onChange={onChange} />)
    const [from, to] = boxes()
    type(from, '1500')
    pressEnter(from)
    expect(screen.getByRole('alert').textContent).toBe('Years run from 1784 to 2026.')
    type(from, '2020')
    type(to, '2000')
    pressEnter(from)
    expect(screen.getByRole('alert').textContent).toBe('The first year must not be after the last.')
    expect(onChange).not.toHaveBeenCalled()
    type(to, '2021')
    pressEnter(from)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onChange).toHaveBeenCalledWith({ from: 2020, to: 2021 })
  })

  it('goes back to all years with the button, and shows a range set from outside', () => {
    const onChange = vi.fn()
    const { rerender } = render(<YearFilter years={{ from: 2000, to: 2020 }} bounds={BOUNDS} onChange={onChange} />)
    expect(boxes().map((box) => box.value)).toEqual(['2000', '2020'])
    act(() => screen.getByRole('button', { name: 'All years' }).click())
    expect(onChange).toHaveBeenCalledWith(null)
    rerender(<YearFilter years={null} bounds={BOUNDS} onChange={onChange} />)
    expect(boxes().map((box) => box.value)).toEqual(['', ''])
  })

  it('does not ask again for the range already shown', () => {
    const onChange = vi.fn()
    render(<YearFilter years={{ from: 2000, to: 2020 }} bounds={BOUNDS} onChange={onChange} />)
    pressEnter(boxes()[0])
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is turned off without the API, and says why', () => {
    render(<YearFilter years={null} bounds={null} onChange={() => {}} />)
    expect(boxes().every((box) => box.disabled)).toBe(true)
    expect(screen.getByText('The year filter needs the species API.')).toBeTruthy()
  })
})
