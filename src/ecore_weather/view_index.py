"""Find completed archive observations without traversing Zarr chunk directories."""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import iso, utc
from .storage import open_raw


def path_day(path):
    match = re.search(r'/((?:19|20)\d{2})/(\d{2})/(\d{2})(?:/|$)', str(path))
    return datetime(*map(int, match.groups()), tzinfo=timezone.utc) if match else None


def in_period(path, start, end):
    """Prune complete date folders before opening any per-observation metadata."""
    day = path_day(path)
    if day:
        return (not start or day+timedelta(days=1) > start) and (not end or day < end)
    match = re.search(r'/((?:19|20)\d{2})(?:/(\d{2}))?/?$', str(path))
    if match:
        year = int(match[1]); month = int(match[2] or 1)
        begin = datetime(year, month, 1, tzinfo=timezone.utc)
        finish = datetime(year+1,1,1,tzinfo=timezone.utc) if not match[2] or month==12 else datetime(year,month+1,1,tzinfo=timezone.utc)
        return (not start or finish > start) and (not end or begin < end)
    return True


def observation(folder, marker):
    name = marker.get('raw_path','raw.zarr')
    if name not in ('raw.zarr','raw.zarr.zip'):
        raise ValueError('Invalid raw container in completion marker.')
    source_url = marker.get('source_url','')
    source = 'goes' if 'noaa-goes' in source_url or '/goes/' in folder else 'mrms' if 'noaa-mrms' in source_url or '/mrms/' in folder else None
    stamp = marker.get('time')
    if not stamp:
        match = re.search(r'_s(\d+)_e',source_url)
        if match:
            from .goes import scan_time
            stamp = iso(scan_time(match[1]))
        else:
            match = re.search(r'(\d{8})-(\d{6})\.grib',source_url)
            if match: stamp = iso(datetime.strptime(''.join(match.groups()),'%Y%m%d%H%M%S'))
    if not stamp:
        day=path_day(folder); match=re.search(r'/(?:C\d{2}-)?(\d{6})-[^/]+$',folder)
        if day and match: stamp=iso(day+timedelta(hours=int(match[1][:2]),minutes=int(match[1][2:4]),seconds=int(match[1][4:])))
    if not stamp:
        with open_raw(folder+'/'+name) as ds:
            stamp=ds.attrs.get('observation_time',ds.attrs.get('time_coverage_start'))
            source='mrms' if 'measurement' in ds else 'goes'
    if not stamp: raise ValueError(f'No observation timestamp: {folder}')
    band = re.search(r'-M\dC(\d{2})_',source_url)
    group = re.split(r'/(?:19|20)\d{2}/\d{2}/\d{2}/',folder)[0]
    return {'path':folder+'/'+name,'time':iso(stamp),'source':source,'band':int(band[1]) if band else None,
            'dataset':group,'asset_id':marker.get('asset_id')}


def months_available(location, source=None):
    """Coarse month availability for a LOCAL archive: which YYYY/MM folders exist.

    Reads only year/month directory names (no completion markers, no chunk
    walks), so it is cheap enough to show before the first Find. Returns a sorted
    list of 'YYYY-MM' strings. Remote (hf://) locations return an empty list:
    listing months there is not cheap, and the Find search reports availability.
    """
    import re
    location = str(location).rstrip('/')
    if location.startswith('hf://') or location.endswith(('.zarr', '.zarr.zip')):
        return []
    root = Path(location).expanduser()
    if source and (root/source).is_dir():
        root = root/source
    if not root.is_dir():
        return []
    months = set()
    year = re.compile(r'(19|20)\d{2}')
    month = re.compile(r'(0[1-9]|1[0-2])')
    for directory, folders, _ in os.walk(root):
        keep = []
        for f in folders:
            if f == 'raw.zarr':
                continue
            if year.fullmatch(f):
                # Read this year's month subfolders, then do not descend into it.
                try:
                    months |= {f'{f}-{m.name}' for m in (Path(directory)/f).iterdir()
                               if m.is_dir() and month.fullmatch(m.name)}
                except OSError:
                    pass
                continue
            keep.append(f)
        folders[:] = keep
    return sorted(months)


def inventory(location, source=None, start=None, end=None, band=None, limit=50000):
    """Date/source filtering precedes the result limit; only completed stores count."""
    start=utc(start) if start else None;end=utc(end) if end else None
    if start and end and start>=end: raise ValueError('End time must follow start time; end is excluded.')
    location=str(location).rstrip('/'); records=[]
    def add(folder,marker):
        row=observation(folder,marker); stamp=utc(row['time'])
        if (not source or row['source']==source) and (not start or stamp>=start) and (not end or stamp<end) and (band is None or row['band'] in (None,band)):
            records.append(row)
    if location.endswith(('.zarr','.zarr.zip')):
        with open_raw(location) as ds:
            actual='mrms' if 'measurement' in ds else 'goes'
            marker={'raw_path':location.rsplit('/',1)[-1],'source_url':ds.attrs.get('source_url',''),
                    'time':ds.attrs.get('observation_time',ds.attrs.get('time_coverage_start'))}
        add(location.rsplit('/',1)[0],marker)
        return records
    if location.startswith('hf://buckets/'):
        from .hf_storage import Publisher
        pub=Publisher(location); prefix=pub.prefix(location)
        if source and f'/{source}/' not in '/'+prefix+'/': prefix+='/'+source
        # Walk only folder levels above a date, then list a requested day at once.
        pending=[prefix]; markers=[]
        while pending:
            current=pending.pop()
            if not in_period('/'+current,start,end):continue
            recursive=path_day('/'+current) is not None
            for obj in pub.api.list_bucket_tree(pub.bucket,prefix=current,recursive=recursive):
                if not hasattr(obj,'size'):
                    if not recursive and not obj.path.endswith('raw.zarr'): pending.append(obj.path)
                elif obj.path.endswith('/complete.json'):
                    markers.append(obj)
        # Bounded batches avoid one request per Zarr chunk. No token goes in a URL.
        for offset in range(0,len(markers),200):
            batch=markers[offset:offset+200]
            with tempfile.TemporaryDirectory(prefix='ecore-view-index-') as temp:
                pub._download([(obj,f'{i}.json') for i,obj in enumerate(batch)],temp)
                for i,obj in enumerate(batch):
                    add(f'hf://buckets/{pub.bucket}/'+obj.path.removesuffix('/complete.json'),json.loads((Path(temp)/f'{i}.json').read_text()))
            if len(records)>=limit:break
    else:
        root=Path(location).expanduser()
        if source and (root/source).is_dir():root=root/source
        if root.is_file() and root.suffix=='.json':
            report=json.loads(root.read_text())
            for row in report.get('records',[]):
                if row.get('url') and row.get('status') in ('saved','reused'):
                    folder,name=row['url'].rsplit('/',1)
                    add(folder,{'raw_path':name,'source_url':row.get('source_url',''),'time':row.get('time'),'asset_id':row.get('asset_id')})
            return sorted(records,key=lambda r:r['time'])
        if not root.is_dir():raise FileNotFoundError(str(root))
        for directory,folders,files in os.walk(root):
            folders[:]=sorted(f for f in folders if f!='raw.zarr' and in_period(str(Path(directory)/f),start,end)
                              and not (source and f in ('mrms','goes') and f!=source))
            if not in_period(directory,start,end):folders[:]=[];continue
            if 'complete.json' in files:
                add(directory,json.loads((Path(directory)/'complete.json').read_text()))
                if len(records)>=limit:break
    if len(records)>=limit:raise ValueError(f'At least {limit} observations match. Choose a shorter period or a narrower product folder.')
    # During migration two locations may describe the same source. Prefer the shared path.
    unique={}
    for row in sorted(records,key=lambda r: '/roi-' in r['path']):
        key=(row['asset_id'] or row['path'],row['dataset'].split('/roi-')[-1] if '/roi-' in row['dataset'] else row['dataset'])
        unique[key]=row
    return sorted(unique.values(),key=lambda r:(r['time'],r['path']))


def daily_sample(records, count=4):
    import numpy as np
    if not 1<=count<=24:raise ValueError('Choose 1–24 images per day.')
    days={}
    for row in records:days.setdefault(row['time'][:10],[]).append(row)
    return [rows[i] for _,rows in sorted(days.items()) for i in np.unique(np.linspace(0,len(rows)-1,min(count,len(rows)),dtype=int))]
