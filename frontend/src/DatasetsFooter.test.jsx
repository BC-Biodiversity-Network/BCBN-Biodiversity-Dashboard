import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import DatasetsFooter from './DatasetsFooter'

afterEach(cleanup)

describe('the Datasets page footer', () => {
  it('credits Lunaris, says each dataset belongs to its repository and whose judgement BC data is', () => {
    render(<DatasetsFooter />)
    const text = screen.getByRole('contentinfo').textContent.replace(/\s+/g, ' ')
    expect(text).toContain('Dataset descriptions from Lunaris (lunaris.ca), harvested 20 October 2026. ' +
      'Each dataset belongs to its original repository; follow its link to view and download it under ' +
      "that repository's terms. Which datasets count as BC data is BCBN's own judgement, worked out from " +
      'the record text.')
    expect(text).not.toContain('classes')
    expect(screen.getByRole('link', { name: 'Lunaris' }).href).toBe('https://www.lunaris.ca/')
  })
})
