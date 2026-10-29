// The datasets that may include BC (national and global data: the API's
// "pending" group), kept apart so they can be taken off the site in one
// place. Supervisors have not decided whether they stay. To take them off,
// set SHOW_WIDER_SWITCH to false: the switch and the tag both go, and only
// BC data (the "bc_data" group) is ever asked for. To remove them for good,
// delete this file and its imports in DatasetsPage.jsx and DatasetDetail.jsx.
export const SHOW_WIDER_SWITCH = true

// The switch on the Datasets page that adds these datasets to the list.
export function WiderSwitch({ checked, onChange }) {
  if (!SHOW_WIDER_SWITCH) {
    return null
  }
  return (
    <label className="datasets-wider">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>Also show datasets that may include BC (national and global)</span>
    </label>
  )
}

// The small tag on one of these datasets, in the list and in its details.
// BC data get no tag.
export function MayIncludeBcTag({ frontendGroup }) {
  if (!SHOW_WIDER_SWITCH || frontendGroup !== 'pending') {
    return null
  }
  return <span className="dataset-tag">May include BC (national or global data)</span>
}
