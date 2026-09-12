# Notebook usage and expected outputs

The two fetching notebooks use the same functions as their command-line interface. In Jupyter,
run the setup cells, edit the controls, and click **Choose data** before fetching.
Creating controls does not transfer data. After changing dates, region, product,
or bands, choose data again to replace the selection used by subsequent actions.

The examples cover September 1–December 1, 2022 and 2025, in UTC with the ending
date excluded. The region starts at the mentor's Puerto Rico window. GOES-East
selects GOES-16 for 2022 and GOES-19 for 2025. An automatic request spanning their
[April 7, 2025 transition](https://www.ospo.noaa.gov/data/messages/2025/04/MSG_20250407_1510.html) must be split or use an explicit satellite.

## Common controls

Choose dates, geographic bounds, product, download workers, storage destination,
and a folder for small run files. GOES also offers satellite and bands; C08/C13
are the examples. **Tasks** defaults to half the available CPU count, at least one. It bounds simultaneous file work, including download and saving. **Readers** selects separate Python reader processes (0 uses threads), capped by Tasks. Hover either control for help. HF publishing remains separately coordinated.
CPU decoding is bounded separately. Independent reader processes can improve
HDF5 access; zero uses threads. Choose directory or ZIP Zarr and an optional fast
local scratch folder. More workers do not guarantee faster reads.

A collapsed **Product and variable guide** sits below the controls. Open it to
see each available product with its units and missing-value codes (MRMS), or the
full-disk imagery products and all sixteen ABI bands (GOES). It is reference
text only; it changes nothing until you choose data again.

**Save to** defaults to the configured HF bucket. Enter a local directory to save
locally. Fetching saves unchanged native subsets as compressed Zarr. It retains
source metadata, timestamps, missing-value codes, and GOES packed values and quality
flags. Completed matching subsets are checked and reused. Failed files appear in
the report; they are never replaced by zero rain.

Select an image index after fetching to display imagery and quality maps. The
**Save displayed figures** checkbox writes PNGs to the figure folder. It does not
save another processed raster. Processing choices only change the displayed data
in memory. Changing the figure folder alone does not save anything.

## MRMS radar

Hourly QPE uses one source observation per nominal hour. The default searches up
to five minutes earlier, so 16:58 can fill 17:00 while retaining 16:58 as the source
time and recording a −120-second offset. Exact matching is available. Nearest
matching can use either side of the hour; positive offsets must not be used as
already-available observations in forecasting input windows. No values are averaged.

Decoding off-hour GRIB files makes the ecCodes library print a "Truncating time"
notice: the file's HHMM time key drops non-zero seconds, and the library reports
this once per file. It is benign — the pipeline takes observation times from the
filenames, not that key, and every saved subset is verified by fingerprint — so
the notice is suppressed during metadata reads to keep long fetches readable.

Inspect Puerto Rico, Mona Passage, Virgin Islands, and offshore patches. Tables
show valid measurements, valid zero, documented missing values, no coverage,
bitmap gaps, and valid-only descriptive statistics. Empty patches remain empty.
Missing source hours are listed separately from missing pixels. The coverage chart
shows changes over time. A centered 512 × 512 view is optional.

Compare the mentor's interpolation and cleaning with masking documented missing
values before interpolation. Both leave stored raw data unchanged. The exact
benchmark reader includes a one-pixel margin; a previously stored crop cannot
provide neighbours beyond its edges.

**Validate** checks every selected file through a raw Zarr round trip and compares
mentor calculations in isolated batches. **Benchmark** runs matched source files
through sequential and concurrent methods. Parallel validation timings are not
speed benchmark results. MRMS gzip downloads still require complete source files.

## GOES imagery

The product control offers full-disk imagery only: the CONUS sector (roughly
20°N–50°N, 125°W–65°W) does not cover Puerto Rico, and this project works in the
Caribbean. CONUS products remain available from the command line for advanced use.

**Scans/hour** controls how many images are kept from each UTC hour. The
default, 1, keeps the scan nearest the top of the hour and downloads far less
than the full inventory; raise it for denser sampling, keeping the scans nearest
to evenly spaced marks. Validation and the long-run scripts request every
available scan explicitly, so their measurements are unaffected.

The ordinary reader selects bands and a geographic window before loading array
chunks. Raw packed pixels and decoded physical values are different views; decoding
uses the preserved calibration. Quality masking and interpolation are optional.

Validation discovers the whole period, then compares six actual scans: nearest to
midnight and noon on the first, middle, and last included days. For these examples
those dates are September 1, October 16, and November 30. Partial-day requests
use available scans; single-band products retain each requested band. Full-file, ranged, and
virtual reads must match every selected array and coordinate. Reference-building
cost is reported separately. Virtual groups keep incompatible encodings apart.

**Build references** saves a small bundle containing Kerchunk references to NOAA's
original objects. It can be reopened with `goes.open_virtual` or, for a compatible
group, `goes.open_virtual_group`. Source objects must remain accessible. The bundle
contains references and inline small metadata, not duplicated satellite imagery.

## Files to expect

| Action | Retained outputs |
| --- | --- |
| Choose / inspect | `collection.json` and `items.json` in the run folder; the latter includes the executable request and STAC Items |
| Fetch | One canonical raw Zarr subset per source object, completion markers, and a run report |
| Display | Inline figures; PNGs only when saving is requested |
| Benchmark | Small timing/equality reports; source files and comparison rasters are temporary |
| Build GOES references | One `index.json` bundle, rather than one JSON per source file |
| Validate | One `validation.json`; temporary Zarr, sources, references, and detailed comparisons are removed |

Use a different run folder to preserve a selection before choosing another one.
Ordinary fetched research data are never deleted by test cleanup. See the
[developer guide](developer_guide.md) for script commands and testing.

## Dataset viewer (03)

Run the setup cells, enter an archive prefix or individual `raw.zarr` /
`raw.zarr.zip` path, and click **Find**. For a local archive a **Month** list
quickly shows which months have stored observations for the selected source
before you search (folder names only, computed once per source); picking one
sets Start and End to that month, and editing a date clears it. A progress bar
and the status line show the search is running, then report observations, days
covered, datasets, and elapsed time. The across-days view selects 1–24 images
per day. The search
is cached for the session, so repeating an identical search is instant. A period
split across subset folders appears as one dataset entry. Select a dataset; for
GOES the **Band** is then chosen from a row of buttons that lists only the bands
actually stored, and switching bands re-reads the same observations without a
new search. A listing shows at most 200 subsets; use a product/date folder for a
large archive. A fetch report JSON is also accepted. Local and
`hf://buckets/namespace/bucket/prefix` locations use the same controls.

Display quality masking and **Hide zero values** change only the figure. Zero
rain is valid. Packed GOES values are decoded in memory and plotted at their
native geographic coordinates; this is not a persisted reprojection. After
**Show map** or preparing a **Day**/**Multi-day** animation, an export row
appears: the single image saves a PNG, and animations save a self-contained
`.html`, a `.gif`, or an `.mp4` (`.mp4` needs an ffmpeg binary). From a terminal:

```bash
python notebooks/03_view_datasets.py /path/to/raw.zarr.zip --hide-zero --output figures/rain.png
```

Natural Earth land/coastlines are downloaded once to Cartopy's cache, then reusable
offline. On an offline cluster, populate that cache in advance. HF ZIP views fetch
one cropped ZIP into temporary scratch; directory stores open through the HF
filesystem. No local tile server is required for these small scientific subsets.
The original MRMS notebook also shows a rain map with this geographic context.

References: [ipywidgets tooltip migration](https://ipywidgets.readthedocs.io/en/latest/user_migration_guides.html#tooltips)
and [Cartopy geographic features](https://cartopy.readthedocs.io/stable/matplotlib/feature_interface.html).
