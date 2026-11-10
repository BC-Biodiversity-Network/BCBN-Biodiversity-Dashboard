// The "Download CSV" button of a list (the Datasets list, a rank card's
// species, a hexagon's species). How to read the file is not repeated
// here: its own 4th line says it.
//
// The button is a plain link to the API's export address (see the
// *ExportUrl functions in api.js): the browser asks the API for the file and
// saves it under the name the API gives, so the page never loads the whole
// file itself.

// The button. href is the export address; disabled (while the
// list loads, when it failed or has nothing in it) shows the button greyed
// out and makes it a link to nowhere, so it cannot be clicked or reached
// with the Tab key. label is what screen readers hear, naming the list.
export default function CsvDownload({ href, disabled = false, label = 'Download this list as a CSV file' }) {
  return (
    <div className="csv-download">
      {disabled ? (
        <a className="csv-download-button disabled" role="link" aria-disabled="true" aria-label={label}>
          Download CSV
        </a>
      ) : (
        <a className="csv-download-button" href={href} download aria-label={label}>
          Download CSV
        </a>
      )}
    </div>
  )
}
