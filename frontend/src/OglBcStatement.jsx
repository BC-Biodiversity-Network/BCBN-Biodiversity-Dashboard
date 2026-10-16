import { OGL_BC_NAME, OGL_BC_PREFIX, OGL_BC_URL } from './sources'

// The attribution statement the Open Government Licence – British Columbia
// asks for, worded exactly as the licence gives it, with the licence's name
// linked to the licence.
export default function OglBcStatement() {
  return (
    <>
      {OGL_BC_PREFIX}
      <a href={OGL_BC_URL} target="_blank" rel="noreferrer">{OGL_BC_NAME}</a>.
    </>
  )
}
