"""Requested long comparisons; temporary rasters are removed by each benchmark."""
import argparse
from pathlib import Path
from ecore_weather import mrms, goes, benchmark, catalog
from ecore_weather.common import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['mrms', 'goes'], required=True)
    parser.add_argument('--output', default='results/comparison-2023')
    args = parser.parse_args()
    root = Path(args.output)/args.source
    selection_path = root/'selection/collection.json'
    if selection_path.exists():
        selection = catalog.load_selection(selection_path)
    else:
        selection = (mrms.discover('2023-01-01', '2023-07-01') if args.source == 'mrms'
                     else goes.discover('2023-01-01', '2023-02-01', satellite=16,
                         product='ABI-L2-CMIPF', bands=(1,2,3,7,8,9,10,13)))
        catalog.save_selection(selection, selection_path.parent)
    print(selection.summary(), flush=True)
    if args.source == 'mrms':
        table, records = benchmark.run_mrms(selection, root, variants=['legacy', 'obstore-process-4', 'obstore-process-8'])
        compact = {'selection': selection.summary(), 'summary': table.to_dict('records'),
                   'comparisons': records.groupby(['variant','status','matches_legacy']).size().reset_index(name='files').to_dict('records')}
    else:
        result = benchmark.run_goes(selection, root)
        compact = {'selection': selection.summary(), 'result': result.to_dict('records')}
    write_json(root/'summary.json', compact)
    if args.source == 'mrms' and (not records.matches_legacy.all() or any(table.succeeded != len(selection.assets))):
        raise AssertionError('Some long-period legacy comparisons failed; inspect the saved report.')

if __name__ == '__main__':
    main()
