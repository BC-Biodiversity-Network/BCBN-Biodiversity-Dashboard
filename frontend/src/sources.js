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

// The BCSEE export is under the Open Government Licence – British Columbia
// (stated in the export file's Metadata sheet and on BCSEE's export page,
// https://a100.gov.bc.ca/pub/eswp/jsp/export.jsp, checked 2026-10-15). The
// licence asks for this attribution statement and, where possible, a link to
// the licence. The wording is the licence's own (version 2.0), en dash
// included; OGL_BC_NAME is the part of it that is linked.
export const OGL_BC_URL =
  'https://www2.gov.bc.ca/gov/content/data/policy-standards/data-policies/open-data/open-government-licence-bc'
export const OGL_BC_PREFIX = 'Contains information licensed under the '
export const OGL_BC_NAME = 'Open Government Licence – British Columbia'
export const OGL_BC_STATEMENT = `${OGL_BC_PREFIX}${OGL_BC_NAME}.`

// The reserve outlines on the map: Natural Resources Canada's "Aboriginal
// Lands of Canada Legislative Boundaries" on the Open Government Portal,
// under the Open Government Licence – Canada, which asks for the statement
// "Contains information licensed under the Open Government Licence –
// Canada." (in the footer, with this link to the licence).
export const RESERVES_DATASET_URL = 'https://open.canada.ca/data/en/dataset/522b07b9-78e2-4819-b736-ad9208eb1067'
export const OGL_CANADA_URL = 'https://open.canada.ca/en/open-government-licence-canada'

// The Open Government Licences of the catalogues whose dataset descriptions
// the Datasets page shows, in the footer's attribution line, each with the
// page of its text found in the November 2026 licence and source checks:
// Canada's own page, the PDF Prince George's records link to, Port Moody's
// terms of use, and Surrey's licence page on its ArcGIS open data site.
// Squamish is named without a link (Lucia's decision, November 2026).
export const DATASETS_OGL_LICENCES = [
  { name: 'Open Government Licence – British Columbia', url: OGL_BC_URL },
  { name: 'Open Government Licence – Canada', url: OGL_CANADA_URL },
  {
    name: 'Open Government License – City of Prince George',
    url: 'https://pgmap.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf',
  },
  { name: 'Open Government Licence – Port Moody', url: 'https://www.portmoody.ca/opendatatou' },
  { name: 'Open Government Licence – Squamish', url: null },
  {
    name: 'Open Government License – City of Surrey',
    url: 'https://opendata-surrey.hub.arcgis.com/pages/55089a19491a4fe59a41e059fd8af708',
  },
]

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
