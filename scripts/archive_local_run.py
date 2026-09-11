"""Move a completed local run to the mounted DAS, validating before cache removal."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import time

from ecore_weather import storage
from ecore_weather.common import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report')
    p.add_argument('--cache-root',required=True)
    p.add_argument('--destination',default='/mnt/p/ecore_eo_datasets')
    p.add_argument('--output',default='results/h2-2022/mrms-archive.json')
    args=p.parse_args()
    report=json.loads(Path(args.report).read_text())
    cache=Path(args.cache_root).resolve();dest=Path(args.destination).resolve()
    mount=subprocess.check_output(['findmnt','-n','-o','SOURCE','-T',str(dest)],text=True).strip()
    if not mount.upper().startswith('P:'):raise RuntimeError('Expected the mounted Windows P: archive.')
    if any(r['status']=='failed' for r in report['records']):raise RuntimeError('Complete failed source files before archiving.')
    before=time.perf_counter()
    def move(row):
        source=Path(row['url']).resolve().parent
        relative=source.relative_to(cache);target=dest/relative
        if not source.exists():
            marker=storage._read_marker(str(target))
            if not marker:raise IOError(f'Missing source and archive marker: {relative}')
            with storage.open_raw(target/marker.get('raw_path','raw.zarr')) as ds:
                if storage.fingerprint(ds)!=marker['array_sha256'] or storage.metadata_fingerprint(ds)!=marker['metadata_sha256']:
                    raise IOError('Previously archived data failed validation.')
        else:
            marker=json.loads((source/'complete.json').read_text())
            if (source/'raw.zarr').exists(): storage.pack_raw(source)
            marker['raw_path']='raw.zarr.zip'
            storage._publish(source,str(target),marker)
            shutil.rmtree(source)
        return {'asset_id':row['asset_id'],'url':str(target/'raw.zarr.zip'),
                'stored_bytes':(target/'raw.zarr.zip').stat().st_size}
    # ZIP compression is already complete; four copies bound DAS seeks and RAM.
    with ThreadPoolExecutor(4) as pool: rows=list(pool.map(move,report['records']))
    summary={'files':len(rows),'stored_bytes':sum(r['stored_bytes'] for r in rows),
             'archive_s':time.perf_counter()-before,'destination':str(dest),
             'cache_copies_removed':True,'records':rows}
    write_json(args.output,summary)
    print({k:v for k,v in summary.items() if k!='records'},flush=True)


if __name__=='__main__':main()
