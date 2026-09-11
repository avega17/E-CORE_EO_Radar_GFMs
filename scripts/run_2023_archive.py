"""Finish H1 MRMS and January CMIPF archives; bounded retries reuse verified data."""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import subprocess
import sys
from ecore_weather.common import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',default='/mnt/p/ecore_eo_datasets')
    parser.add_argument('--output',default='results/archive-2023')
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--read-processes',type=int,default=4)
    parser.add_argument('--attempts',type=int,default=3)
    args=parser.parse_args()
    if args.attempts<1:parser.error('Attempts must be positive.')
    root=Path(__file__).resolve().parents[1];os.chdir(root)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    jobs=[('mrms','2023-07-01'),('goes','2023-02-01')]
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(),'destination':args.destination,'jobs':[],'complete':False}
    write_json(out/'status.json',state)
    for source,end in jobs:
        job={'source':source,'start':'2023-01-01','end_excluded':end,'attempts':[],'complete':False}
        state['jobs'].append(job)
        for attempt in range(1,args.attempts+1):
            state['active_source']=source;state['active_attempt']=attempt;write_json(out/'status.json',state)
            command=[sys.executable,'scripts/fetch_long_sample.py','--source',source,'--start','2023-01-01','--end',end,
                     '--selection',f'results/comparison-2023/{source}/selection/collection.json',
                     '--destination',args.destination,'--output',str(out),'--container','zip',
                     '--workers',str(args.workers),'--read-processes',str(args.read_processes)]
            print(f'Starting {source}, attempt {attempt}/{args.attempts}',flush=True)
            with (out/f'{source}.log').open('a') as log:
                result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
            job['attempts'].append({'attempt':attempt,'exit_code':result.returncode})
            job['complete']=result.returncode==0;write_json(out/'status.json',state)
            if job['complete']:break
        print(f"{source}: {'complete' if job['complete'] else 'needs attention'}",flush=True)
    state['complete']=all(job['complete'] for job in state['jobs'])
    state['finished_utc']=datetime.now(timezone.utc).isoformat();state.pop('active_source',None);state.pop('active_attempt',None)
    write_json(out/'status.json',state)
    return 0 if state['complete'] else 1

if __name__=='__main__':raise SystemExit(main())
