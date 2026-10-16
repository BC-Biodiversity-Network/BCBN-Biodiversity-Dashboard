// What each BCSEE "B.C. List" value means, shown when a status chip on the
// species card is hovered, focused or tapped.
//
// The words are BCSEE's own, from its help page on the B.C. List
// (BCSEE_LIST_HELP_URL, read on 15 October 2026), not ours. Only the
// first sentence or two of each definition is used, and the rank codes in
// brackets (such as "(i.e., Provincial Conservation Status Rank is SNR)")
// are left out; nothing else is reworded.
export const BCSEE_LIST_HELP_URL = 'https://www.env.gov.bc.ca/atrisk/help/list.htm'

export const BCSEE_LIST_DEFINITIONS = {
  Red:
    'Includes any native species or ecological communities that have, or are ' +
    'candidates for, Extirpated, Endangered, or Threatened status in British Columbia.',
  Blue:
    'Includes any native species or ecological community considered to be of ' +
    'Special Concern (formerly Vulnerable) in British Columbia.',
  Yellow:
    'Includes species or ecological communities that are apparently secure and not ' +
    'at risk of extinction.',
  Exotic:
    'Species that have been moved beyond their natural range as a result of human activity.',
  Accidental:
    'Species occurring infrequently and unpredictably, outside their usual range.',
  Unknown:
    'Includes species or ecological communities for which the Provincial Conservation ' +
    'Status is unknown due to extreme uncertainty.',
  'No Status':
    'No Status is assigned to the animal species record when all related infraspecies ' +
    '(e.g., subspecies, population, ecotype, etc.) in BC have been assigned to the Red, ' +
    'Blue, or Yellow List. No Status is assigned to plant hybrids (i.e. hybrids without ' +
    'conservation value).',
  'Not Reviewed':
    'Species and ecological communities that have not undergone a conservation status ' +
    'assessment.',
  Extinct:
    'Species that no longer exist.',
}

// Returns BCSEE's definition of a B.C. List value, or, for a value BCSEE's
// help page does not define, a sentence saying so.
export function bcseeDefinition(value) {
  return BCSEE_LIST_DEFINITIONS[value] ??
    `"${value}" is a value on BCSEE's B.C. List that its help page does not define.`
}
