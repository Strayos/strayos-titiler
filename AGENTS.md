# About

* This project is called strayos-titiler which is a fork of titiler. https://developmentseed.org/titiler/
* STRAYOS.md contains the working documentation of the work done including production and deployment configs along side assumtions made. it should be read before attempting anything  updated upon making changes.

## Instructions

* run pycompile after making changes to the code and test if the code compiles
* after making changes check if STRAYOS.md contains data which is outdated and update the info
* dont make changes to the dockerfiles without understanding the nginx configs for the files
* read your way to the project through the compose files

## TiTiler stress test

The `stress-test/` directory stress-tests the TiTiler VM. It requests tiles from more rasters than the six TiTiler workers so the test can displace server-side raster cache entries and measure uncached blob-fetch performance. The test models the OpenLayers frontend, which does not set a tile-fetch timeout or replace concurrent requests.

### Stress-test instructions

* use the repository-root virtual environment before running Python scripts: `source .venv/bin/activate`
* add concise docstrings and type hints to stress-test code
* run pycompile, Black, and isort after changing stress-test Python code