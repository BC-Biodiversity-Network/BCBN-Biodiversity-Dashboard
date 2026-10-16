// The citations and licences of the sources the site shows, used by the
// Sources section of the species card and the page footer. Each one is the
// source's own recommended wording; where it came from is noted next to it.

// The Catalogue of Life release the names come from: its official citation
// as ChecklistBank gives it for the release (https://api.checklistbank.org/
// dataset/315557, field "citation"), and its licence (field "license",
// "cc by", which ChecklistBank's licence list defines as CC BY 4.0). This
// must be updated when the data moves to a new release; the card only shows
// it for names from this release.
export const COL_RELEASES = {
  315557: {
    authors:
      'Bánki, O., Roskov, Y., Döring, M., Ower, G., Hernández Robles, D. R., ' +
      'Plata Corredor, C. A., Stjernegaard Jeppesen, T., Örn, A., Pape, T., Hobern, D., ' +
      'Garnett, S., Little, H., DeWalt, R. E., Miller, J., Orrell, T., Aalbu, R., ' +
      'Abbott, J., Abreu, C., Acero P, A., et al. (2026).',
    title: 'Catalogue of Life',
    rest: '(2026-06-19 XR). Catalogue of Life Foundation, Amsterdam, Netherlands.',
    doiUrl: 'https://doi.org/10.48580/dgy8b',
    licenceLabel: 'CC BY 4.0',
    licenceUrl: 'https://creativecommons.org/licenses/by/4.0/',
  },
}

// Finds the Catalogue of Life release in a card's col_release text, for
// example "COL26.6 XR (ChecklistBank 315557)", and returns its citation, or
// null for a release not listed in COL_RELEASES.
export function colRelease(colReleaseText) {
  const match = /ChecklistBank (\d+)/.exec(colReleaseText ?? '')
  return (match && COL_RELEASES[match[1]]) || null
}

// The BC Species and Ecosystems Explorer, cited the way the BC Conservation
// Data Centre asks ("How to Cite Information & Data from the Conservation
// Data Centre", BCSEE_CITATION_GUIDE_URL): the year of the export, and when
// it was retrieved.
export const BCSEE_URL = 'http://a100.gov.bc.ca/pub/eswp/'
export const BCSEE_CITATION_GUIDE_URL =
  'https://www2.gov.bc.ca/gov/content/environment/plants-animals-ecosystems/conservation-data-centre/cite-cdc-data'

// Writes the start of the BCSEE citation, up to the link (which the page
// adds), for an export made on exportDate (YYYY-MM-DD).
export function bcseeCitation(exportDate) {
  const year = (exportDate ?? '').slice(0, 4)
  return `B.C. Conservation Data Centre. ${year}. BC Species and Ecosystems Explorer. ` +
    'B.C. Government, Victoria B.C. Available:'
}

// The text after the BCSEE link in its citation.
export function bcseeCitationEnd(exportDate, pulledOn) {
  return `(provincial export of ${exportDate}, accessed ${pulledOn}).`
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December']

// Writes an ISO date as GBIF writes it in a download citation, for example
// "4 October 2026". Uses the date in UTC, as GBIF does.
export function citationDate(isoDate) {
  const date = new Date(isoDate)
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`
}
