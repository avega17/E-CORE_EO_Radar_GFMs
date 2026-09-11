from dataclasses import replace

import numpy as np
import pytest
import xarray as xr

from ecore_weather import diagnostics, goes, mrms
from ecore_weather.catalog import load_selection, save_selection
from ecore_weather.common import Asset, Selection, validate_request
from ecore_weather.storage import fingerprint, open_raw, write_raw


def radar(values, product=mrms.DEFAULT_PRODUCT):
    values = np.asarray(values, dtype="float64")
    ds = xr.Dataset({"measurement": (("latitude", "longitude"), values),
                     "bitmap_valid": (("latitude", "longitude"), np.ones(values.shape, "uint8"))},
                    coords={"latitude": np.linspace(19, 17, values.shape[0]),
                            "longitude": np.linspace(292, 295, values.shape[1])},
                    attrs={"product": product, "observation_time": "2022-09-18T00:00:00Z"})
    ds.measurement.attrs["units"] = mrms.PRODUCTS[product]["unit"]
    return ds


def test_sentinels_and_valid_zero():
    ds = radar([[0, 2, -1], [-3, np.nan, 6]])
    table = diagnostics.describe(ds, {"all": (-69, 16, -64, 20)})
    row = table.iloc[0]
    assert row.valid_count == 3
    assert row.valid_zero_count == 1
    assert row.missing_fill_count == row.no_coverage_count == row.nonfinite_count == 1
    assert row["mean"] == pytest.approx(8/3)
    assert sum(row[f"{name}_count"] for name in diagnostics.CLASS_NAMES.values()) == 6


def test_negative_reflectivity_is_valid_and_bitmap_is_separate():
    ds = radar([[-10, -99], [-999, 3]], "MergedReflectivityQCComposite_00.50")
    ds.bitmap_valid.values[1, 1] = 0
    codes, _ = diagnostics.classify(ds, "measurement")
    np.testing.assert_array_equal(codes, [[0, 1], [2, 3]])


def test_all_invalid_patch_and_outside_patch():
    ds = radar([[-1, -3], [-1, -3]])
    table = diagnostics.describe(ds, {"all": (-69, 16, -64, 20), "outside": (-85, 0, -80, 5)})
    assert table.iloc[0].note == "no valid measurements"
    assert np.isnan(table.iloc[0]["mean"])
    assert table.iloc[1].pixels == 0
    assert np.isnan(table.iloc[1].valid_pct)


def test_raw_roundtrip_preserves_sentinels_and_metadata(tmp_path):
    ds = radar([[0, -1], [-3, 12.3]])
    ds.measurement.attrs.update(scale_note="No new rounding", missingValue=9999)
    before = fingerprint(ds)
    write_raw(ds, tmp_path / "raw.zarr")
    with open_raw(tmp_path / "raw.zarr") as actual:
        assert fingerprint(actual) == before
        assert actual.measurement.attrs == ds.measurement.attrs


def test_packed_goes_roundtrip_and_quality_mask(tmp_path):
    raw = xr.Dataset({"CMI_C13": (("y", "x"), np.array([[100, -1], [200, 300]], "int16")),
                      "DQF_C13": (("y", "x"), np.array([[0, 3], [1, 0]], "int8"))},
                     coords={"x": [0, 1], "y": [0, 1]})
    raw.CMI_C13.attrs = {"_FillValue": np.int16(-1), "scale_factor": np.float32(.1),
                        "add_offset": np.float32(200), "_Unsigned": "true", "units": "K"}
    raw.x.attrs = {"scale_factor": .000056, "add_offset": -.151844}
    raw.y.attrs = {"scale_factor": -.000056, "add_offset": .151844}
    before = fingerprint(raw)
    write_raw(raw, tmp_path / "goes.zarr")
    with open_raw(tmp_path / "goes.zarr") as actual:
        assert fingerprint(actual) == before
        processed = goes.process(actual)
        assert processed.shape == (2, 2)
        assert processed.values[0, 0] == pytest.approx(210)
        assert np.isnan(processed.values[0, 1])
        assert np.isnan(processed.values[1, 0])
    assert fingerprint(raw) == before


def test_interpolation_recipes_do_not_mutate_raw():
    ds = radar([[1, -3, 3], [1, 2, 3], [0, 0, 0]])
    before = fingerprint(ds)
    for recipe in ["legacy_exact", "quality_aware"]:
        processed = mrms.process(ds, recipe=recipe, bbox=(-68, 17, -65, 19), shape=(8, 8))
        assert processed.shape == (8, 8)
        assert fingerprint(ds) == before


def test_stac_selection_roundtrip_and_half_open_interval(tmp_path):
    a = Asset("bucket", "a.grib2.gz", 123, "etag", "2022-09-18T00:00:00Z")
    selection = Selection("mrms", mrms.DEFAULT_PRODUCT, a.time, "2022-09-19T00:00:00Z",
                          (-70, 15, -62, 22), [a], expected_times=(a.time,))
    path = save_selection(selection, tmp_path / "catalog")
    actual = load_selection(path)
    assert actual.id == selection.id
    assert actual.assets == selection.assets
    assert load_selection(tmp_path / "catalog/items.json").id == selection.id
    with pytest.raises(ValueError):
        validate_request("2022-09-19", "2022-09-18", selection.bbox)


def test_native_crop_keeps_source_order_and_values():
    ds = radar(np.arange(12).reshape(3, 4))
    crop = mrms.crop_native(ds, (-67.1, 16.5, -64.9, 19.5))
    np.testing.assert_array_equal(crop.longitude, [293, 294, 295])
    np.testing.assert_array_equal(crop.measurement, ds.measurement.values[:, 1:])


def test_goes_scan_time_and_sample_selection():
    stamp = goes.scan_time("20222610000202")
    assert stamp.second == 20 and stamp.microsecond == 200000
    assets = [Asset("b", f"a{i}", 1, "e", f"2022-09-{day:02d}T{hour:02d}:00:20Z")
              for i, (day, hour) in enumerate((d, h) for d in range(18, 25) for h in range(24))]
    s = Selection("goes", "ABI-L2-MCMIPF", "2022-09-18", "2022-09-25", (-70, 15, -62, 22), assets)
    picked = goes.benchmark_assets(s)
    assert len(picked) == 6
    assert [a.time[:10] for a in picked] == ["2022-09-18"]*2+["2022-09-21"]*2+["2022-09-24"]*2


def test_fetch_resume_and_failure_cleanup(tmp_path, monkeypatch):
    from ecore_weather import storage
    good = Asset('b', 'good', 1, 'e', '2024-09-15T00:00:00Z')
    bad = Asset('b', 'bad', 1, 'e', '2024-09-15T01:00:00Z')
    selection = Selection('mrms', mrms.DEFAULT_PRODUCT, good.time, '2024-09-15T02:00:00Z',
                          (-69, 16, -64, 20), [good, bad])
    calls = []
    def read(asset, *args, **kwargs):
        calls.append(asset.key)
        if asset.key == 'bad':
            raise IOError('deliberately unavailable source')
        return radar([[0, -1], [-3, 12.3]]), {'download_s': 0, 'decode_crop_s': 0}
    monkeypatch.setattr(mrms, 'read', read)
    scratch = tmp_path / 'scratch'
    report = storage.fetch(selection, destination=tmp_path/'data', scratch=scratch,
                           report_dir=tmp_path/'reports', workers=2)
    assert [r['status'] for r in report['records']] == ['saved', 'failed']
    assert list(scratch.iterdir()) == []
    assert not list((tmp_path/'data'/selection.id/bad.id).glob('complete.json'))
    again = storage.fetch(selection, destination=tmp_path/'data', scratch=scratch,
                          report_dir=tmp_path/'reports', workers=1)
    assert again['records'][0]['status'] == 'reused'
    assert calls.count('good') == 1
    assert list(scratch.iterdir()) == []


def test_legacy_comparison_rejects_off_hour_file():
    from ecore_weather.benchmark import comparable_hours, run_mrms
    asset = Asset('b', 'a', 1, 'e', '2022-09-24T16:58:00Z')
    selection = Selection('mrms', mrms.DEFAULT_PRODUCT, '2022-09-24', '2022-09-25',
                          (-70.24, 14.36, -62.56, 22.04), [asset],
                          expected_times=('2022-09-24T17:00:00Z',))
    assert not comparable_hours(selection).assets
    with pytest.raises(ValueError, match='Off-hour'):
        run_mrms(selection)


def test_storage_requires_explicit_destination():
    from ecore_weather.storage import destination_root
    with pytest.raises(ValueError):
        destination_root('')
    with pytest.raises(ValueError):
        destination_root('s3://unsupported-durable-destination')


def test_hour_matching_preserves_actual_time_and_prevents_future_default():
    assets = [Asset("b", str(i), 1, "e", t) for i, t in enumerate([
        "2022-09-24T16:58:00Z", "2022-09-24T18:02:00Z"])]
    slots = ("2022-09-24T17:00:00Z", "2022-09-24T18:00:00Z")
    picked, matches = mrms.match_hours(assets, slots)
    assert picked == [assets[0]]
    assert matches[0]["offset_seconds"] == -120
    assert matches[0]["source_time"] == assets[0].time
    assert not mrms.match_hours(assets, slots, method="exact")[0]
    assert len(mrms.match_hours(assets, slots, method="nearest")[0]) == 2
    with pytest.raises(ValueError):
        mrms.match_hours(assets, slots, tolerance_minutes=30)


def test_compact_catalog_retains_hour_matches(tmp_path):
    asset = Asset("b", "a", 1, "e", "2022-09-24T16:58:00Z")
    slots = ("2022-09-24T17:00:00Z",)
    assets, matches = mrms.match_hours([asset], slots)
    selection = Selection("mrms", mrms.DEFAULT_PRODUCT, slots[0], "2022-09-24T18:00:00Z",
                          (-70, 15, -62, 22), assets, expected_times=slots,
                          hourly_matches=matches, time_tolerance_minutes=5, time_match="previous")
    actual = load_selection(save_selection(selection, tmp_path))
    assert actual.id == selection.id
    assert actual.summary()["missing_times"] == []
    import pystac
    from ecore_weather.common import utc
    collection = pystac.Collection.from_file(str(tmp_path / "collection.json"))
    assert collection.extent.temporal.intervals[0][0] == utc(asset.time)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["collection.json", "items.json"]


def test_cli_and_satellite_defaults():
    import multiprocessing
    from ecore_weather.cli import parser
    from ecore_weather.common import default_workers
    assert default_workers() == max(1, multiprocessing.cpu_count()//2)
    args = parser("mrms").parse_args(["--period", "2025", "--workers", "3", "--save-figures", "plots"])
    assert args.workers == 3 and args.save_figures == "plots"
    assert goes.east_satellite("2022-09-01", "2022-12-01") == 16
    assert goes.east_satellite("2025-09-01", "2025-12-01") == 19
    with pytest.raises(ValueError):
        goes.east_satellite("2025-04-01", "2025-05-01")


def test_validation_only_cleans_data_and_preserves_slot(tmp_path, monkeypatch):
    from ecore_weather import storage
    asset = Asset("b", "a", 1, "e", "2022-09-24T16:58:00Z")
    slots = ("2022-09-24T17:00:00Z",)
    assets, matches = mrms.match_hours([asset], slots)
    selection = Selection("mrms", mrms.DEFAULT_PRODUCT, slots[0], "2022-09-24T18:00:00Z",
                          (-70, 15, -62, 22), assets, expected_times=slots, hourly_matches=matches)
    ds = radar([[0, 1], [-1, -3]])
    ds.attrs["observation_time"] = asset.time
    monkeypatch.setattr(mrms, "read", lambda *a, **kw: (ds, {}))
    report = storage.fetch(selection, workers=1, validate_only=True, scratch=tmp_path,
                           report_dir=None, inspect=lambda d: diagnostics.describe(d).to_dict("records"))
    row = report["records"][0]
    assert row["status"] == "validated" and row["url"] is None
    assert row["diagnostics"][0]["time"] == asset.time
    assert row["diagnostics"][0]["slot_time"] == slots[0]
    assert not list(tmp_path.iterdir())


def test_coverage_uses_matched_slot_without_losing_actual_time():
    import matplotlib.pyplot as plt
    ds = radar([[0, 1], [2, 3]])
    ds.attrs.update(observation_time="2022-09-24T16:58:00Z", hourly_slot="2022-09-24T17:00:00Z")
    table = diagnostics.describe(ds, {"all": (-69, 16, -64, 20)})
    fig = diagnostics.plot_coverage(table, (ds.attrs["hourly_slot"], "2022-09-24T18:00:00Z"))
    assert len(fig.axes[0].lines[0].get_ydata()) == 2
    np.testing.assert_array_equal(fig.axes[0].lines[0].get_ydata(), [100, np.nan])
    assert table.iloc[0].time == ds.attrs["observation_time"]
    plt.close(fig)


def test_json_reports_use_null_for_unavailable_statistics(tmp_path):
    import json
    from ecore_weather.common import write_json
    path = tmp_path / "report.json"
    write_json(path, {"median": np.nan, "bytes": None})
    assert "NaN" not in path.read_text()
    assert json.loads(path.read_text()) == {"median": None, "bytes": None}


def test_goes_coverage_reports_empty_hours_and_known_scan_counts():
    assets = [Asset("b", f"OR_ABI-L2-MCMIPF-M6_G16_{i}", 1, "e",
                    f"2022-09-01T00:{i*10:02d}:20Z") for i in range(6)]
    selection = Selection("goes", "ABI-L2-MCMIPF", "2022-09-01", "2022-09-01T02:00:00Z",
                          (-70, 15, -62, 22), assets, bands=(8,13))
    coverage = goes.acquisition_coverage(selection)
    assert coverage.observed_files.tolist() == [6, 0]
    assert coverage.iloc[0].expected_files == 6
    assert np.isnan(coverage.iloc[1].expected_files)


def test_goes_partial_day_sample_uses_available_scans_and_each_band():
    assets = [Asset("b", f"OR_ABI-L2-CMIPF-M6C{band:02d}_G19", 1, "e",
                    "2025-09-01T13:10:20Z") for band in (8,13)]
    selection = Selection("goes", "ABI-L2-CMIPF", "2025-09-01T13:00:00Z", "2025-09-01T14:00:00Z",
                          (-70, 15, -62, 22), assets, bands=(8,13))
    assert goes.benchmark_assets(selection) == assets


def test_script_returns_failure_for_benchmark_mismatch(tmp_path, monkeypatch):
    import pandas as pd
    from ecore_weather import benchmark, cli
    asset = Asset("b", "a", 1, "e", "2022-09-01T00:00:00Z")
    selection = Selection("mrms", mrms.DEFAULT_PRODUCT, asset.time, "2022-09-01T01:00:00Z",
                          (-70, 15, -62, 22), [asset])
    monkeypatch.setattr(mrms, "discover", lambda **kw: selection)
    monkeypatch.setattr(benchmark, "run_mrms", lambda *a, **kw: (pd.DataFrame(), pd.DataFrame({"matches_legacy": [False]})))
    assert cli.main("mrms", ["--operation", "benchmark", "--output", str(tmp_path)]) == 1


def test_hf_batch_verifies_before_completion_and_resumes(tmp_path):
    from types import SimpleNamespace
    from ecore_weather.hf_storage import Publisher
    from ecore_weather.storage import metadata_fingerprint
    class API:
        def __init__(self): self.files = {}; self.adds = []; self.corrupt = False
        def batch_bucket_files(self, bucket, *, add):
            self.adds.append([name for _, name in add])
            for source, name in add:
                self.files[name] = source if isinstance(source, bytes) else source.read_bytes()
        def get_bucket_paths_info(self, bucket, paths):
            return [SimpleNamespace(path=p) for p in paths if p in self.files]
        def download_bucket_files(self, bucket, files, **kw):
            for remote, local in files:
                data = self.files[remote if isinstance(remote, str) else remote.path]
                local.write_bytes(b'corrupt' if self.corrupt else data)
    api = API(); pub = Publisher('hf://buckets/u/b/test', api=api)
    ds = radar([[0, -1], [-3, 4]])
    write_raw(ds, tmp_path/'raw.zarr')
    marker = dict(selection_id='s', asset_id='a', raw_schema_version=1,
                  array_sha256=fingerprint(ds), metadata_sha256=metadata_fingerprint(ds))
    pub.publish(tmp_path, 'hf://buckets/u/b/test', marker)
    assert api.adds[-1] == ['test/complete.json']
    assert pub.resume('hf://buckets/u/b/test','s','a',1)
    api.corrupt = True
    with pytest.raises(Exception): pub.publish(tmp_path, 'hf://buckets/u/b/bad', marker)
    assert 'bad/complete.json' not in api.files


def test_hf_writer_serializes_threads():
    from concurrent.futures import ThreadPoolExecutor
    from ecore_weather.hf_storage import bucket_writer
    import time
    active = peak = 0
    def work(_):
        nonlocal active, peak
        with bucket_writer('unit-test/bucket'):
            active += 1; peak = max(peak, active)
            time.sleep(.005)
            active -= 1
    with ThreadPoolExecutor(16) as pool: list(pool.map(work, range(16)))
    assert peak == 1


def test_zip_container_preserves_arrays_metadata_and_removes_directory(tmp_path):
    from ecore_weather.storage import pack_raw, metadata_fingerprint
    ds = radar([[0,-1],[-3,4]])
    write_raw(ds,tmp_path/'raw.zarr')
    path = pack_raw(tmp_path)
    assert not (tmp_path/'raw.zarr').exists()
    with open_raw(path) as actual:
        assert fingerprint(actual) == fingerprint(ds)
        assert metadata_fingerprint(actual) == metadata_fingerprint(ds)


def test_quiet_eccodes_notices_suppresses_fd2_and_restores(capfd):
    import os
    from ecore_weather.mrms import _ECCODES_NOTICE_LOCK, _quiet_eccodes_notices
    os.write(2, b"before\n")
    with _quiet_eccodes_notices():
        os.write(2, b"hidden\n")
    os.write(2, b"after\n")
    err = capfd.readouterr().err
    assert "hidden" not in err
    assert "before" in err and "after" in err
    assert not _ECCODES_NOTICE_LOCK.locked()


def test_quiet_eccodes_notices_restores_after_exception(capfd):
    import os
    from ecore_weather.mrms import _ECCODES_NOTICE_LOCK, _quiet_eccodes_notices
    with pytest.raises(RuntimeError):
        with _quiet_eccodes_notices():
            raise RuntimeError("boom")
    assert not _ECCODES_NOTICE_LOCK.locked()
    os.write(2, b"recovered\n")
    assert "recovered" in capfd.readouterr().err


def test_metadata_get_quiets_only_time_keys(capfd):
    import os
    from ecore_weather.mrms import _metadata_get

    class FakeEC:
        @staticmethod
        def codes_get(handle, key):
            if key in mrms._METADATA_TIME_KEYS:
                os.write(2, f"ECCODES ERROR   :  Key {key} (unpack_long): Truncating time\n".encode())
            return 1658

    for key in ("dataDate", "dataTime", "validityDate", "validityTime"):
        assert _metadata_get(FakeEC, None, key) == 1658
    assert "ECCODES ERROR" not in capfd.readouterr().err
    assert _metadata_get(FakeEC, None, "discipline") == 1658


def test_widget_products_hide_conus_and_cheatsheet_collapsed():
    import ipywidgets as widgets
    from ecore_weather import ui
    controls = ui.selection_controls("goes")
    options = controls["product"].options
    assert "ABI-L2-MCMIPF" in options and "ABI-L2-CMIPF" in options
    assert not any(option.endswith("C") for option in options)
    cheatsheet = controls["cheatsheet"]
    assert isinstance(cheatsheet, widgets.Accordion)
    assert cheatsheet.selected_index is None
    html = cheatsheet.children[0].value
    assert "ABI-L2-MCMIPF" in html and "CONUS" in html and "C13" in html


def test_mrms_cheatsheet_lists_products_and_sentinels():
    from ecore_weather import ui
    controls = ui.selection_controls("mrms")
    assert list(controls["product"].options) == list(mrms.PRODUCTS)
    cheatsheet = controls["cheatsheet"]
    assert cheatsheet.selected_index is None
    html = cheatsheet.children[0].value
    for product in mrms.PRODUCTS:
        assert product in html
    assert "dBZ" in html and "−1 / −3" in html and "−99 / −999" in html


def test_overlapping_dates_reuse_raw_and_container(tmp_path, monkeypatch):
    from ecore_weather import storage
    asset = Asset('b', 'good', 1, 'e', '2024-09-15T00:00:00Z')
    selection = Selection('mrms', mrms.DEFAULT_PRODUCT, asset.time, '2024-09-16', (-69,16,-64,20), [asset])
    calls = []
    def read(*args, **kwargs):
        calls.append(1)
        return radar([[0,-1],[-3,2]]), {}
    monkeypatch.setattr(mrms, 'read', read)
    first = storage.fetch(selection, tmp_path, workers=1, report_dir=None, container='zip')
    wider = replace(selection, start='2024-09-01', end='2024-12-01')
    second = storage.fetch(wider, tmp_path, workers=1, report_dir=None, container='directory')
    assert second['records'][0]['status'] == 'reused'
    assert first['records'][0]['url'] == second['records'][0]['url']
    assert calls == [1]
    assert not list(tmp_path.rglob('raw.zarr'))
    assert storage.subset_identity(replace(selection, bbox=(-70,16,-64,20))) != storage.subset_identity(selection)


def test_cmip_identity_does_not_depend_on_other_requested_bands():
    from ecore_weather.storage import subset_identity
    request = Selection('goes', 'ABI-L2-CMIPF', '2023-01-01', '2023-02-01', (-69,16,-64,20), [], bands=(1,13), satellite=16)
    assert subset_identity(request) == subset_identity(replace(request, bands=(1,2,3,13)))
    multi = replace(request, product='ABI-L2-MCMIPF')
    assert subset_identity(multi) != subset_identity(replace(multi, bands=(1,2,3,13)))


def test_earlier_dated_store_adoption_checks_raw(tmp_path, monkeypatch):
    from ecore_weather import storage
    asset = Asset('b', 'good', 1, 'e', '2024-09-15T00:00:00Z')
    selection = Selection('mrms', mrms.DEFAULT_PRODUCT, asset.time, '2024-09-16', (-69,16,-64,20), [asset])
    old = tmp_path/storage.product_path(selection)/'2024-09-15_2024-09-16-old'/'2024/09/15'/f'000000-{asset.id}'
    ds = radar([[0,-1],[-3,2]]); ds.attrs['requested_bbox'] = list(selection.bbox)
    storage.write_raw(ds, old/'raw.zarr')
    storage.write_json(old/'complete.json', dict(asset_id=asset.id, selection_id='old', raw_schema_version=1,
        array_sha256=storage.fingerprint(ds), metadata_sha256=storage.metadata_fingerprint(ds)))
    monkeypatch.setattr(mrms, 'read', lambda *a, **kw: pytest.fail('must reuse existing raw'))
    result = storage.fetch(selection, tmp_path, workers=1, report_dir=None)
    assert result['records'][0]['status'] == 'reused'
    assert not old.exists()
    assert len(list(tmp_path.rglob('complete.json'))) == 1


def test_worker_tooltips_and_viewer_preserve_raw(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    from ecore_weather import ui, viewer, maps
    monkeypatch.setattr(maps, "_land_polygons", lambda: [])
    controls = ui.selection_controls('mrms')
    assert controls['read_processes'].description == 'Readers'
    assert '0 uses threads' in controls['read_processes'].tooltip
    assert 'Hugging Face' in controls['workers'].tooltip
    ds = radar([[0,1],[-1,-3]])
    ds.attrs['requested_bbox'] = [-69,16,-64,20]
    path = tmp_path/'raw.zarr'
    write_raw(ds, path)
    before = fingerprint(ds)
    assert viewer.stores(path) == [str(path)]
    viewer.draw(path, output=tmp_path/'rain.png', display=False, hide_zero=True)
    assert (tmp_path/'rain.png').stat().st_size > 1000
    with open_raw(path) as actual:
        assert fingerprint(actual) == before


def test_single_band_goes_accepts_one_element_band_dimension():
    ds = xr.Dataset({'CMI':(('y','x'),np.ones((2,2),'int16')),
                     'DQF':(('y','x'),np.zeros((2,2),'int8')),
                     'band_id':('band',[2]), 'band_wavelength':('band',[.64])})
    names = goes.selected_variables(ds, (1,2,3,7,8,9,10,13))
    assert {'CMI','DQF','band_id','band_wavelength'} <= set(names)
    with pytest.raises(ValueError, match='requested band'):
        goes.selected_variables(ds, (8,13))


def test_merge_duplicate_archive_retains_unrelated_files(tmp_path):
    import importlib.util
    from pathlib import Path
    from ecore_weather import storage
    spec = importlib.util.spec_from_file_location('archive_merge', Path(__file__).parents[1]/'scripts/merge_local_archive.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    asset = Asset('b','good',1,'e','2024-09-15T00:00:00Z')
    selection = Selection('mrms',mrms.DEFAULT_PRODUCT,asset.time,'2024-09-16',(-69,16,-64,20),[asset])
    ds=radar([[0,-1],[-3,2]]);ds.attrs['requested_bbox']=list(selection.bbox)
    old=[]
    for period in ('2024-09-01_2024-10-01-a','2024-09-15_2024-09-16-b'):
        path=tmp_path/storage.product_path(selection)/period/'2024/09/15'/f'000000-{asset.id}'
        storage.write_raw(ds,path/'raw.zarr')
        storage.write_json(path/'complete.json',dict(asset_id=asset.id,raw_schema_version=1,
            array_sha256=storage.fingerprint(ds),metadata_sha256=storage.metadata_fingerprint(ds)))
        old.append(path)
    (old[1]/'research-note.txt').write_text('Keep this note')
    report=module.merge(selection,tmp_path)
    assert report['complete']
    assert report['counts']['moved']==1 and report['counts']['duplicates_removed']==1
    notes=list(tmp_path.rglob('research-note.txt'))
    assert len(notes)==1 and notes[0].read_text()=='Keep this note'
    assert len(list(tmp_path.rglob('raw.zarr')))==1
    again=module.merge(selection,tmp_path)
    assert again['counts']['already_shared']==1 and again['complete']


def test_concurrent_overlapping_runs_publish_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from ecore_weather import storage
    asset=Asset('b','good',1,'e','2024-09-15T00:00:00Z')
    selection=Selection('mrms',mrms.DEFAULT_PRODUCT,asset.time,'2024-09-16',(-69,16,-64,20),[asset])
    calls=[]
    def read(*args, **kwargs):
        calls.append(1)
        time.sleep(.05)
        return radar([[0,-1],[-3,2]]),{}
    monkeypatch.setattr(mrms,'read',read)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(storage.fetch,s,tmp_path,workers=1,report_dir=None) for s in
                 (selection,replace(selection,start='2024-09-01',end='2024-12-01'))]
        outcomes=[f.result()['records'][0]['status'] for f in futures]
    assert sorted(outcomes)==['reused','saved']
    assert calls==[1]


def test_interrupt_cancels_queued_fetches_and_records_progress(tmp_path, monkeypatch):
    import time,json
    from ecore_weather import storage
    assets=[Asset('b',f'file-{i}',1,'e',f'2024-09-15T{i:02d}:00:00Z') for i in range(20)]
    selection=Selection('mrms',mrms.DEFAULT_PRODUCT,'2024-09-15','2024-09-16',(-69,16,-64,20),assets)
    calls=[]
    def read(*args,**kwargs):
        calls.append(1);time.sleep(.02)
        return radar([[0,-1],[-3,2]]),{}
    monkeypatch.setattr(mrms,'read',read)
    def interrupt(*args):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        storage.fetch(selection,tmp_path/'data',scratch=tmp_path/'scratch',workers=1,
                      report_dir=tmp_path/'reports',progress=interrupt)
    report=json.loads(next((tmp_path/'reports').glob('*.json')).read_text())
    assert report['interrupted'] and report['not_started']>0
    assert 1 <= len(calls) < len(assets)
    assert len(report['records'])==len(calls)
    assert not list((tmp_path/'scratch').iterdir())
    assert all(r['status']=='saved' for r in report['records'])
