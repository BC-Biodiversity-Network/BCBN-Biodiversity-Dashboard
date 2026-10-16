import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import InfoTip from './InfoTip'

afterEach(cleanup)

describe('InfoTip', () => {
  it('a tap opens it, and a tap elsewhere closes it', () => {
    render(<div><InfoTip label="?" ariaLabel="Help">Some help.</InfoTip><p>elsewhere</p></div>)
    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    expect(screen.getByRole('tooltip').textContent).toBe('Some help.')
    expect(screen.getByRole('button', { name: 'Help' }).getAttribute('aria-expanded')).toBe('true')
    fireEvent.pointerDown(screen.getByText('elsewhere'))
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('a tap inside the tip itself does not close it', () => {
    render(<InfoTip label="?" ariaLabel="Help">Some help.</InfoTip>)
    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    fireEvent.pointerDown(screen.getByRole('tooltip'))
    expect(screen.getByRole('tooltip')).toBeTruthy()
  })
})
