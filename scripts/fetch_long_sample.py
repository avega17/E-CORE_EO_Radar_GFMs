"""Fetch a long NOAA selection locally; keep raw subsets and resumable progress."""
import argparse
from pathlib import Path
import time
import os

from ecore_weather import catalog, goes, mrms, storage
from ecore_weather.common import default_workers, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start', default='2022-07-01')
    p.add_argument('--end', default='2023-01-01')
    p.add_argument('--selection', help='Reuse an existing STAC collection; requires one source and matching start/end dates')
    p.add_argument('--product', help='MRMS or GOES product, when discovering a new selection')
    p.add_argument('--bands', nargs='+', type=int, help='GOES bands; default C08-C11/C13-C16')
    p.add_argument('--satellite', type=int, default=16)
    p.add_argument('--source', choices=['mrms','goes','both'], default='both')
    p.add_argument('--destination', required=True, help='Explicit local cache or mounted archive directory')
    p.add_argument('--scratch', default='/tmp/ecore-long-scratch')
    p.add_argument('--workers', type=int, default=default_workers())
    p.add_argument('--read-processes', type=int, default=8)
    p.add_argument('--container', choices=['zip','directory'], default='zip')
    p.add_argument('--output', default='results/h2-2022')
    args=p.parse_args()
    if args.selection and args.source=='both':p.error('Use --source mrms or goes with --selection.')
    if args.workers<1 or args.read_processes<0:p.error('Workers must be positive and reader processes nonnegative.')
    out=Path(args.output); out.mkdir(parents=True, exist_ok=True)
    if Path(args.destination).resolve().is_relative_to('/mnt/p') and not os.path.ismount('/mnt/p'):
        raise RuntimeError('Windows P: is not mounted at /mnt/p.')
    if '://' in args.destination: p.error('This measurement is local; choose a local destination.')
    for source in ['mrms','goes'] if args.source=='both' else [args.source]:
        before=time.perf_counter(); directory=out/source
        saved=directory/'collection.json'
        if args.selection or saved.exists():
            selection=catalog.load_selection(args.selection or saved)
            if selection.source!=source:raise ValueError('Selection source does not match --source.')
            if args.product and selection.product!=args.product:raise ValueError('Selection product differs from --product.')
            if args.bands and tuple(sorted(args.bands))!=selection.bands:raise ValueError('Selection bands differ from --bands.')
            if args.selection:catalog.save_selection(selection,directory)
            if selection.start[:10]!=args.start or selection.end[:10]!=args.end:
                raise ValueError('Output folder contains a different date selection; choose another output folder.')
        else:
            selection=(mrms.discover(args.start,args.end,product=args.product or mrms.DEFAULT_PRODUCT) if source=='mrms' else
                       goes.discover(args.start,args.end,satellite=args.satellite,product=args.product or 'ABI-L2-MCMIPF',
                                     bands=tuple(args.bands or (8,9,10,11,13,14,15,16))))
            catalog.save_selection(selection,directory)
        discovery_s=time.perf_counter()-before
        write_json(out/f'{source}-progress.json',dict(stage='fetch',selection=selection.summary()))
        print(source,selection.summary(),flush=True)
        counts={'saved':0,'reused':0,'failed':0}
        def progress(done,total,row):
            counts[row['status']]=counts.get(row['status'],0)+1
            if done%25==0 or done==total:
                state=dict(stage='fetch',completed=done,total=total,last_status=row['status'],
                           elapsed_s=time.perf_counter()-before,counts=dict(counts))
                write_json(out/f'{source}-progress.json',state);print(source,state,flush=True)
        report=storage.fetch(selection,destination=args.destination,workers=args.workers,
                             backend='obstore',scratch=args.scratch,report_dir=directory,
                             progress=progress,read_processes=args.read_processes,container=args.container)
        result={k:v for k,v in report.items() if k!='records'}
        result.update(discovery_s=discovery_s,total_s=time.perf_counter()-before,
                      saved=sum(r['status']=='saved' for r in report['records']),
                      reused=sum(r['status']=='reused' for r in report['records']),
                      failures=[r for r in report['records'] if r['status']=='failed'],
                      source_catalog_bytes=sum(a.size for a in selection.assets))
        write_json(out/f'{source}-summary.json',result)
        write_json(out/f'{source}-progress.json',dict(stage='failed' if result['failures'] else 'complete',completed=len(report['records']),total=len(selection.assets),
                                                                        saved=result['saved'],reused=result['reused'],failures=len(result['failures'])))
        if result['failures']: raise RuntimeError(f"{source}: {len(result['failures'])} files failed; rerun to retry.")


if __name__=='__main__': main()
