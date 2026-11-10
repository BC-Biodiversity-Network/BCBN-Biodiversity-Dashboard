import OglBcStatement from './OglBcStatement'
import { OGL_CANADA_URL, RESERVES_DATASET_URL } from './sources'

// The small footer along the bottom of every page, below the map, whether
// or not a species card is open: where the data comes from, that BCBN does
// not set conservation statuses, the attribution statement the Open
// Government Licence – British Columbia asks for (the BCSEE export is under
// it), where the reserve outlines come from, with the statement the Open
// Government Licence – Canada asks for, the base map credit (always visible here,
// even when the map's own credit is folded away on a small screen), and
// that the sources do not endorse the site and that it is not an official
// government source (which their licences, the OGL-BC among them, ask users
// not to suggest).
export default function SiteFooter() {
  return (
    <footer className="site-footer">
      Conservation statuses come from the{' '}
      <a href="http://a100.gov.bc.ca/pub/eswp/" target="_blank" rel="noreferrer">
        BC Species and Ecosystems Explorer
      </a>{' '}
      (BCSEE), maintained by the BC Conservation Data Centre. BCBN shows them as published
      and does not assess or set conservation statuses. <OglBcStatement /> Species names and authorship come
      from the{' '}
      <a href="https://www.catalogueoflife.org/" target="_blank" rel="noreferrer">Catalogue of Life</a>,
      via GBIF. Occurrence records come from{' '}
      <a href="https://www.gbif.org/" target="_blank" rel="noreferrer">GBIF.org</a>. Reserve outlines:{' '}
      <a href={RESERVES_DATASET_URL} target="_blank" rel="noreferrer">
        Aboriginal Lands of Canada Legislative Boundaries
      </a>, Natural Resources Canada, simplified for display; not to be used for defining boundaries. Contains
      information licensed under the{' '}
      <a href={OGL_CANADA_URL} target="_blank" rel="noreferrer">Open Government Licence – Canada</a>.
      Base map:{' '}
      <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a>,{' '}
      <a href="https://openmaptiles.org/" target="_blank" rel="noreferrer">© OpenMapTiles</a>,{' '}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
        © OpenStreetMap contributors
      </a>. These sources,
      and the photographers whose photos are shown, do not endorse BCBN or this site, and this
      site is not an official government source.
    </footer>
  )
}
