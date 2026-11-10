"""
frontend_node.py

Runs a small piece of the front end's own JavaScript with node, so a test
can check that the API and the front end give exactly the same answer (the
GBIF links, the hexagons under a box).

The front end is built with Vite, which can import a file as plain text by
adding "?raw" to its name (gbifLinks.js reads BC's boundary that way).
Plain node cannot, so node is started with two small hooks that do what
Vite does: a "?raw" import gives the file's text.

Tests that use this are skipped when node or the front end's packages
(npm install in frontend/) are missing.
"""

import shutil
import subprocess
from pathlib import Path

# The front end's folder.
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# Whether node and the front end's packages are here.
NODE_READY = shutil.which("node") is not None and (FRONTEND / "node_modules" / "h3-js").exists()

# The hooks: a "?raw" import is found next to the file that asks for it, and
# loads as a module whose default export is the file's text.
RAW_HOOKS = """
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export async function resolve(specifier, context, nextResolve) {
  if (specifier.endsWith('?raw')) {
    return { url: new URL(specifier, context.parentURL).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('?raw')) {
    const file = new URL(url)
    file.search = ''
    const text = readFileSync(fileURLToPath(file), 'utf8')
    return { format: 'module', source: `export default ${JSON.stringify(text)}`, shortCircuit: true }
  }
  return nextLoad(url, context)
}
"""

# Puts the hooks in place before the test's script runs.
REGISTER = """
import { register } from 'node:module'
register('./raw-hooks.mjs', import.meta.url)
"""


def front_end_module(name):
    """Return the address node imports a front end file by, such as
    front_end_module("gbifLinks.js") for frontend/src/gbifLinks.js."""
    return (FRONTEND / "src" / name).as_uri()


def run_node(folder, script):
    """Run a JavaScript module (the text of script) with node, with "?raw"
    imports working as in Vite, from the folder given (a test's temporary
    folder), and return what it printed. Stops the test with node's own
    message if the script fails."""
    folder = Path(folder)
    (folder / "raw-hooks.mjs").write_text(RAW_HOOKS)
    (folder / "register.mjs").write_text(REGISTER)
    (folder / "script.mjs").write_text(script)
    done = subprocess.run(["node", "--import", (folder / "register.mjs").as_uri(), str(folder / "script.mjs")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout
