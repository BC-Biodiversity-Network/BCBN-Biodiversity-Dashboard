// Builds the little box that appears when the pointer is over a hexagon on
// the overview map: the number of records and of species in it. Returning
// null means no hexagon is under the pointer, so no box is shown.
//
// The hexagon's H3 cell id is not shown: it means nothing to a visitor.
export function getTooltip({ object }) {
  if (!object) {
    return null
  }

  return {
    html: `
      <div><strong>${object.occurrences.toLocaleString()}</strong> records</div>
      <div><strong>${object.distinctSpecies.toLocaleString()}</strong> species</div>
    `,
  }
}
