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

// Builds the box that appears when the pointer is over a hexagon on the
// species map: how many records of this species are in it. Returning null
// means no hexagon is under the pointer, so no box is shown.
export function getSpeciesTooltip({ object }) {
  if (!object) {
    return null
  }

  const count = object.occurrences
  return {
    html: `<div><strong>${count.toLocaleString()}</strong> ${count === 1 ? 'record' : 'records'} of this species</div>`,
  }
}
