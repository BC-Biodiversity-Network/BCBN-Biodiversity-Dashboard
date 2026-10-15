"""
main.py

The species search API: a small FastAPI app that reads the precomputed
species tables with DuckDB.

Run it from backend/ (the Vite dev server forwards /api to port 8000):

    uvicorn api.main:app --reload --port 8000

When it starts it checks the data folder (see settings.py and db.py) and
refuses to start if anything is missing or wrong, printing every problem.

Endpoints so far:
    GET /api/meta   where the data comes from and what it holds: the
                    Catalogue of Life release, the BCSEE source and dates,
                    the GBIF download to cite, species and record totals,
                    the year range, the map resolutions and the
                    sensitive-species setting.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from api.db import open_data
from api.settings import load_settings


def create_app(settings=None):
    """Build the API. settings defaults to the environment variables (see
    settings.py); the tests pass their own to point at small test files."""

    @asynccontextmanager
    async def lifespan(app):
        """Open the data when the API starts and close it when it stops.
        The settings are read here, not when this file is imported, so a
        bad setting or a missing file stops the start-up with a clear
        message."""
        app.state.data = open_data(settings or load_settings())
        try:
            yield
        finally:
            app.state.data.close()

    app = FastAPI(title="BCBN species API", lifespan=lifespan)

    @app.get("/api/meta")
    def meta(request: Request):
        """Return where the data comes from and what it holds. The figures
        are worked out once at start-up, since they only change with the
        data files."""
        return request.app.state.data.meta

    return app


# The app uvicorn runs (api.main:app).
app = create_app()
