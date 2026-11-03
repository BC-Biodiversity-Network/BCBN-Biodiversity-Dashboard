import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import DatasetsFooter from './DatasetsFooter'

afterEach(cleanup)

describe('the Datasets page footer', () => {
  it('credits Lunaris, says each dataset belongs to its repository and whose judgement BC data is', () => {
    render(<DatasetsFooter />)
    const text = screen.getByRole('contentinfo').textContent.replace(/\s+/g, ' ')
    expect(text).toContain('Dataset descriptions from Lunaris (lunaris.ca), harvested 20 October 2026. ')
    expect(text).toContain('Each dataset belongs to its original repository; follow its link to view and ' +
      'download it under ' +
      "that repository's terms. Which datasets count as BC data is BCBN's own judgement, worked out from " +
      'the record text.')
    expect(text).not.toContain('classes')
    expect(screen.getByRole('link', { name: 'Lunaris' }).href).toBe('https://www.lunaris.ca/')
  })

  it('gives the attribution the Open Government Licences ask for, each linked but Squamish', () => {
    render(<DatasetsFooter />)
    const text = screen.getByRole('contentinfo').textContent.replace(/\s+/g, ' ')
    expect(text).toContain('harvested 20 October 2026. Contains information licensed under the Open Government '
      + 'Licence – British Columbia, the Open Government Licence – Canada, the Open Government License – City of '
      + 'Prince George, the Open Government Licence – Port Moody, the Open Government Licence – Squamish and the '
      + 'Open Government License – City of Surrey. Each dataset belongs')
    const links = {
      'Open Government Licence – British Columbia':
        'https://www2.gov.bc.ca/gov/content/data/policy-standards/data-policies/open-data/open-government-licence-bc',
      'Open Government Licence – Canada': 'https://open.canada.ca/en/open-government-licence-canada',
      'Open Government License – City of Prince George':
        'https://pgmap.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf',
      'Open Government Licence – Port Moody': 'https://www.portmoody.ca/opendatatou',
      'Open Government License – City of Surrey':
        'https://opendata-surrey.hub.arcgis.com/pages/55089a19491a4fe59a41e059fd8af708',
    }
    for (const [name, url] of Object.entries(links)) {
      expect(screen.getByRole('link', { name }).href).toBe(url)
    }
    expect(screen.queryByRole('link', { name: 'Open Government Licence – Squamish' })).toBeNull()
  })
})
