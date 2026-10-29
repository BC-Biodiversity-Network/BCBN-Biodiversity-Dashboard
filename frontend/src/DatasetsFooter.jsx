// The footer of the Datasets page: where the dataset descriptions come
// from, that each dataset belongs to its own repository, that which
// datasets count as BC data is BCBN's own judgement, and the base map
// credit (always visible here, as on the species map). The GeoNames credit
// is shown next to the places it was used for, in a dataset's details.
export default function DatasetsFooter() {
  return (
    <footer className="site-footer">
      Dataset descriptions from{' '}
      <a href="https://www.lunaris.ca/" target="_blank" rel="noreferrer">Lunaris</a> (lunaris.ca), harvested
      20 October 2026. Each dataset belongs to its original repository; follow its link to view and
      download it under that repository's terms. Which datasets count as BC data is BCBN's own judgement,
      worked out from the record text. Base map:{' '}
      <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a>,{' '}
      <a href="https://openmaptiles.org/" target="_blank" rel="noreferrer">© OpenMapTiles</a>,{' '}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
        © OpenStreetMap contributors
      </a>.
    </footer>
  )
}
