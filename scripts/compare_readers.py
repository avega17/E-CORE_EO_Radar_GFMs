"""Measure raw-Zarr reader concurrency on the same representative CMIP files."""
import argparse
from dataclasses import replace
from pathlib import Path
import tempfile
from ecore_weather import catalog, goes, storage
from ecore_weather.common import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection')
    parser.add_argument('--output',default='results/comparison-2023/goes/readers.json')
    args=parser.parse_args()
    original=catalog.load_selection(args.selection)
    selected=replace(original,assets=goes.benchmark_assets(original))
    summaries=[]
    reference=None
    for processes in (0,4,8):
        with tempfile.TemporaryDirectory(prefix='ecore-reader-comparison-') as temp:
            report=storage.fetch(selected,Path(temp)/'data',workers=16,read_processes=processes,
                                 container='zip',report_dir=None)
            failed=[r for r in report['records'] if r['status']!='saved']
            if failed: raise RuntimeError(failed)
            hashes={r['asset_id']: storage._read_marker(str(Path(r['url']).parent))['array_sha256'] for r in report['records']}
            if reference is None: reference=hashes
            if hashes!=reference: raise AssertionError('Reader configurations returned different raw arrays')
            compact={k:v for k,v in report.items() if k not in ('records','root','selection_summary')}
            compact.update(files=len(hashes),all_arrays_match=True,temporary_raw_removed=True)
        summaries.append(compact)
        write_json(args.output,{'period_selection_id':original.id,'representative_files':len(selected.assets),'runs':summaries})
        print(compact,flush=True)

if __name__=='__main__': main()
