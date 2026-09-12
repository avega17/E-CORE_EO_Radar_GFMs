"""Browse a local archive, HF prefix, run report, or individual raw Zarr subset."""
import argparse
import json
import re
from pathlib import Path

from .storage import open_raw
from .maps import plot_geographic


def stores(location, limit=200):
    """List bounded results; choose a product/date prefix for a large archive."""
    location = str(location).rstrip('/')
    if location.endswith(('.zarr', '.zarr.zip')):
        return [location]
    if location.startswith('hf://buckets/'):
        from .hf_storage import Publisher
        publisher = Publisher(location)
        import tempfile
        markers = []
        for obj in publisher.api.list_bucket_tree(publisher.bucket, prefix=publisher.prefix(location), recursive=True):
            if obj.path.endswith('/complete.json'):
                markers.append(obj)
                if len(markers) >= limit:
                    break
        paths = []
        if markers:
            # One batch for completion metadata rather than per-chunk filesystem calls.
            with tempfile.TemporaryDirectory(prefix='ecore-view-list-') as temp:
                publisher._download([(obj, f'{i}.json') for i,obj in enumerate(markers)], temp)
                for i,obj in enumerate(markers):
                    marker = json.loads((Path(temp)/f'{i}.json').read_text())
                    name = marker.get('raw_path','raw.zarr')
                    if name not in ('raw.zarr','raw.zarr.zip'):
                        raise ValueError('Invalid raw container in completion marker.')
                    paths.append(f'hf://buckets/{publisher.bucket}/{obj.path.removesuffix("complete.json")}{name}')
        return sorted(paths)
    root = Path(location).expanduser()
    if root.is_file() and root.suffix == '.json':
        report = json.loads(root.read_text())
        return [r['url'] for r in report.get('records', []) if r.get('url') and r.get('status') in ('saved','reused')][:limit]
    if not root.is_dir():
        raise FileNotFoundError(location)
    # Walk stops before entering chunk directories; no recursive chunk inventory.
    import os
    paths = []
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if f != 'raw.zarr')
        if 'complete.json' in files:
            marker = json.loads((Path(directory)/'complete.json').read_text())
            name = marker.get('raw_path', 'raw.zarr')
            if name not in ('raw.zarr', 'raw.zarr.zip'):
                raise ValueError('Invalid raw container in completion marker.')
            paths.append(str(Path(directory)/name))
            if len(paths) >= limit:
                break
    return sorted(paths)


def draw(path, variable=None, quality=True, hide_zero=False, output=None, display=True):
    import matplotlib.pyplot as plt
    with open_raw(path) as ds:
        fig = plot_geographic(ds, variable, quality=quality, hide_zero=hide_zero)
        try:
            if output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(output, dpi=150)
            if display:
                from IPython.display import display as show
                show(fig)
        finally:
            plt.close(fig)
    return str(output) if output else None


def controls():
    import html
    from datetime import date, datetime, time, timedelta, timezone
    import ipywidgets as w
    from IPython.display import display
    from . import view_index, view_frames, ui
    from .storage import destination_root
    source=w.ToggleButtons(options=[('MRMS radar','mrms'),('GOES satellite','goes')],description='Source')
    storage_kind=w.ToggleButtons(options=['Local','Hugging Face'],description='Storage')
    location=w.Text(value='/mnt/p/ecore_eo_datasets',description='Location',layout=w.Layout(width='95%'),
                    tooltip='Local: archive root, product folder, or raw Zarr. HF: hf://buckets/namespace/bucket/noaa-subsets or a narrower product folder. An existing hf-mount path works as Local.')
    help_text=w.HTML('Local example: <code>/mnt/p/ecore_eo_datasets</code>. For HF choose Hugging Face; the configured bucket is filled in automatically. '
                     'Your token stays in the environment. No hf-mount or tile server is required. Choose dates in UTC; the ending instant is excluded.')
    start_day=w.DatePicker(value=date(2022,7,1),description='Start UTC',tooltip='First included date. Combine with the adjacent UTC time.')
    end_day=w.DatePicker(value=date(2022,7,2),description='End UTC',tooltip='Ending date/time is excluded; next-day midnight includes a whole day.')
    start_time=w.TimePicker(value=time(0),description='Time',tooltip='Start time in UTC, included.')
    end_time=w.TimePicker(value=time(0),description='Time',tooltip='End time in UTC, excluded.')
    month=w.Select(options=[],description='Month',rows=4,
                   tooltip='Months that have stored observations in this local archive. Selecting one sets Start and End to that month; editing a date clears it. Choose a manual range for more than one month.')
    month_note=w.HTML('Local archives list available months before you search.')
    find=w.Button(description='Find',icon='search',button_style='primary',tooltip='Find observations in the selected archive and period.')
    find_bar=w.IntProgress(value=0,min=0,max=1,bar_style='info',layout=w.Layout(width='180px',visibility='hidden'))
    dataset=w.Dropdown(options=[],description='Dataset',layout=w.Layout(width='95%'),
                      tooltip='Choose one product/satellite/region. Observation times use the slider below, not this menu.')
    band=w.ToggleButtons(options=[],description='Band',disabled=True,
                    tooltip='Satellite band for the view. Switching bands re-reads the same observations without a new search. Only stored bands are listed.')
    status=w.HTML();errors=w.Output()
    state={'records':[],'selected':[],'frames':None,'name':None,'mode':None,'inv_key':None,'inventory':{},'bands':{},'months':{}}
    quality=w.Checkbox(value=True,description='Hide invalid pixels',tooltip='Apply documented quality masks to display copies only.')
    zero=w.Checkbox(value=True,description='Hide zero values',tooltip='Make valid zero rainfall transparent for display. Stored zeros remain unchanged.')
    index=w.IntSlider(min=0,max=0,value=0,description='Observation',continuous_update=False)
    stamp=w.HTML('Find observations to select an image.')
    export_path=w.Text(value='figures/dataset-view.png',description='Export path',layout=w.Layout(width='70%'),
                       tooltip='Single image: .png. Animations: .html, .gif, or .mp4 (mp4 needs ffmpeg).')
    export_button=w.Button(description='Export',icon='download',disabled=True,tooltip='Export the displayed view to the chosen path.')
    export_row=w.HBox([export_path,export_button]);export_row.layout.display='none'
    single_button=w.Button(description='Show map',icon='map',tooltip='Display the selected observation on an interactive map.');single_output=w.Output()
    day=w.DatePicker(value=start_day.value,description='Day UTC',tooltip='One day within the search bounds; every available observation is included.')
    daily_button=w.Button(description='Day',icon='play',tooltip='Prepare every available observation on the chosen day as an animation.');daily_output=w.Output()
    per_day=w.IntSlider(value=4,min=1,max=24,description='Frames/day',continuous_update=False,
                       tooltip='Evenly select up to 24 available observations per day (one per hour); no temporal interpolation or gap filling. More frames take longer to prepare.')
    multi_button=w.Button(description='Multi-day',icon='film',tooltip='Prepare 4–8 observations per day across the period as an animation.');multi_output=w.Output()
    tabs=w.Tab(children=[w.VBox([index,stamp,single_button,single_output]),
                         w.VBox([day,w.HTML('All available samples on this day within the search bounds. Prepare once, then use Play or drag the frame slider.'),daily_button,daily_output]),
                         w.VBox([per_day,w.HTML('Use the start/end bounds above. Each day contributes available samples; missing days remain absent. Colors stay fixed across the sequence.'),multi_button,multi_output])])
    for i,label in enumerate(['Single image','Within one day','Across days']):tabs.set_title(i,label)

    def bounds():
        if not start_day.value or not end_day.value:raise ValueError('Choose start and end dates.')
        return (datetime.combine(start_day.value,start_time.value or time(0),tzinfo=timezone.utc),
                datetime.combine(end_day.value,end_time.value or time(0),tzinfo=timezone.utc))

    def refresh_months(change=None):
        key=(location.value,source.value)
        if location.value.startswith('hf://'):
            month.options=[];month_note.value='Remote archives: available months appear in the search result.'
            return
        # Compute availability only for the currently selected source, once per
        # location; toggling the source computes that source on demand.
        if key not in state['months']:
            month_note.value='Scanning local months…'
            state['months'][key]=view_index.months_available(location.value,source.value)
        months=state['months'][key]
        month.options=months
        month_note.value=(f'{len(months)} month(s) with stored observations in this archive.' if months
                          else 'No stored observations found for this source/location.')
    def change_storage(change):
        try:location.value=destination_root('hf') if change['new']=='Hugging Face' else '/mnt/p/ecore_eo_datasets'
        except ValueError as error:status.value=html.escape(str(error))
        refresh_months()
    storage_kind.observe(change_storage,names='value')
    def clear_search(change=None):
        state['records']=[];state['selected']=[];state['frames']=None;state['mode']=None;dataset.options=[];index.max=0
        stamp.value='Parameters changed: click Find again.'
        export_row.layout.display='none';export_button.disabled=True
        band.options=[];band.disabled=source.value=='mrms'
        for out in (single_output,daily_output,multi_output):out.clear_output()
    _applying_month={'flag':False}
    def clear_month(change):
        if _applying_month['flag']:return
        if month.value is not None:month.value=None
    for control in (start_day,end_day,start_time,end_time):control.observe(clear_search,names='value')
    for control in (start_day,end_day,start_time,end_time):control.observe(clear_month,names='value')
    source.observe(refresh_months,names='value');location.observe(refresh_months,names='value')
    def apply_month(change):
        if change['name']!='value' or change['new'] is None:return
        year,mon=map(int,change['new'].split('-'))
        _applying_month['flag']=True
        try:
            start_day.value=date(year,mon,1)
            end_day.value=date(year+mon//12,mon%12+1,1)
            start_time.value=time(0);end_time.value=time(0)
        finally:_applying_month['flag']=False
    month.observe(apply_month,names='value')
    refresh_months()

    def update_stamp(change=None):
        rows=state['selected']
        stamp.value=html.escape(f"{rows[index.value]['time']} · {index.value+1}/{len(rows)}") if rows else 'No observations selected.'
    index.observe(update_stamp,names='value')
    def choose_dataset(change):
        base=change['new']
        rows=[r for r in state['records'] if re.sub(r'/roi-[0-9a-f]+$','',r['dataset'].split('/'+source.value+'/')[-1])==base]
        state['selected']=rows;index.max=max(0,len(rows)-1);index.value=0;update_stamp()
        refresh_bands()
    dataset.observe(choose_dataset,names='value')

    def refresh_bands():
        if source.value=='mrms':
            band.options=[];band.disabled=True;return
        group=dataset.value
        if group not in state['bands']:
            bands=sorted({r['band'] for r in state['selected'] if r['band']})
            if not bands:
                from . import goes
                collected=set()
                for r in state['selected']:
                    with open_raw(r['path']) as ds:
                        collected |= {int(v.rsplit('_C',1)[-1]) for v in goes.science_variables(ds)}
                    if collected and len(collected) >= 16:
                        break
                bands=sorted(collected)
            state['bands'][group]=bands
        bands=state['bands'][group]
        band.options=[(f'C{b:02d}',b) for b in bands]
        band.disabled=not bands
        if bands and band.value not in bands:band.value=bands[-1]

    def search(_):
        import time as _time
        with errors:
            errors.clear_output(wait=True);find.disabled=True
            find_bar.layout.visibility='visible';find_bar.bar_style='info'
            started=_time.perf_counter();status.value='Searching the archive…'
            try:
                start,end=bounds()
                key=(location.value,source.value,start.isoformat(),end.isoformat())
                if key not in state['inventory']:
                    state['inventory']={(location.value,source.value,start.isoformat(),end.isoformat()):
                        view_index.inventory(location.value,source.value,start,end,None)}
                    state['inventory']={key:state['inventory'][key]}
                rows=state['inventory'][key]
                state['records']=rows;state['bands']={}
                # Consolidate one logical dataset per product/satellite: a period
                # split across several subset folders (roi-...) is one view entry.
                def logical(g):
                    base=g.split('/'+source.value+'/')[-1]
                    return re.sub(r'/roi-[0-9a-f]+$','',base)
                groups=sorted({logical(r['dataset']) for r in rows})
                dataset.options=groups
                choose_dataset({'new':dataset.value})
                day.value=start_day.value
                days=len({r['time'][:10] for r in rows})
                elapsed=_time.perf_counter()-started
                status.value=(f'{len(rows):,} observations across {days} day(s) in {len(groups)} dataset(s), '
                              f'found in {elapsed:.1f}s. Choose a dataset, then a view.')
                if not rows:status.value+=' No matches: check the dates, source, and location.'
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:find.disabled=False;find_bar.layout.visibility='hidden'
    find.on_click(search)

    def variable():return 'measurement' if source.value=='mrms' else None
    def prepare(rows,pixels):
        if not rows:raise ValueError('Find observations and choose a dataset first.')
        # CMIP uses CMI; multiband stores use CMI_Cnn.
        name=variable()
        if source.value=='goes':
            with open_raw(rows[0]['path']) as ds:name='CMI' if 'CMI' in ds else f'CMI_C{band.value:02d}'
        with ui.FetchProgress(len(rows),'Display frames') as progress:
            return view_frames.prepare(rows,name,quality.value,zero.value,pixels,progress=progress),name
    def show_export(frames,name,mode):
        state['frames'],state['name'],state['mode']=frames,name,mode
        export_button.disabled=False;export_row.layout.display=None
        default={'single':'figures/dataset-view.png','day':'figures/dataset-day.html','multi':'figures/dataset-multi.html'}[mode]
        if export_path.value in ('figures/dataset-view.png','figures/dataset-day.html','figures/dataset-multi.html') or not export_path.value:
            export_path.value=default
    def render_single(_):
        with single_output:
            single_output.clear_output(wait=True);single_button.disabled=True
            try:
                rows=state['selected'];chosen=[rows[index.value]] if rows else []
                frames,name=prepare(chosen,768)
                display(view_frames.leaflet(frames));show_export(frames,name,'single')
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:single_button.disabled=False
    single_button.on_click(render_single)
    def animate(mode):
        out=daily_output if mode=='day' else multi_output
        button=daily_button if mode=='day' else multi_button
        with out:
            out.clear_output(wait=True);button.disabled=True
            try:
                rows=state['selected']
                if mode=='day':
                    if day.value is None:raise ValueError('Choose a day.')
                    rows=[r for r in rows if r['time'][:10]==day.value.isoformat()]
                else:rows=view_index.daily_sample(rows,per_day.value)
                frames,name=prepare(rows,384);display(view_frames.leaflet(frames));show_export(frames,name,mode)
                print(f'{len(frames)} frames prepared. Playback reads no additional Zarr data. Display limit: 300 frames.')
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:button.disabled=False
    daily_button.on_click(lambda _:animate('day'));multi_button.on_click(lambda _:animate('multi'))
    def do_export(_):
        with errors:
            errors.clear_output(wait=True);export_button.disabled=True
            try:
                frames,name,mode=state['frames'],state['name'],state['mode']
                if frames is None:raise ValueError('Show an image or prepare an animation first.')
                if mode=='single':
                    print(draw(frames[0]['path'],name,quality.value,zero.value,export_path.value,display=False))
                else:
                    print(view_frames.save_animation(frames,export_path.value))
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:export_button.disabled=False
    export_button.on_click(do_export)
    def band_changed(change):
        if change['name']!='value' or change.get('old')==change.get('new'):return
        if state['frames'] is None or source.value=='mrms':return
        # Re-read the same observations for the new band; no new search.
        if state['mode']=='single':render_single(None)
        elif state['mode'] in ('day','multi'):animate(state['mode'])
    band.observe(band_changed,names='value')
    panel=w.VBox([source,storage_kind,location,help_text,w.HBox([start_day,start_time]),w.HBox([end_day,end_time]),
                  month,month_note,w.HBox([find,find_bar]),status,dataset,band,w.HBox([quality,zero]),errors,tabs,export_row])
    panel._ecore_controls={'source':source,'location':location,'start':start_day,'end':end_day,'start_time':start_time,
                          'end_time':end_time,'tabs':tabs,'search':find,'dataset':dataset,'band':band,'single':single_button,
                          'day':daily_button,'multi':multi_button,'state':state,'index':index,'export':export_button,
                          'export_row':export_row,'export_path':export_path,'month':month}
    display(panel)
    return panel


def main(argv=None):
    from . import view_index,view_frames,ui
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('location',help='Local/HF archive, one dataset folder, report JSON, or raw Zarr')
    parser.add_argument('--source',choices=['mrms','goes'],help='Filter to one sensor family')
    parser.add_argument('--start',help='Included UTC date/time')
    parser.add_argument('--end',help='Excluded UTC date/time')
    parser.add_argument('--band',type=int,default=13,help='GOES channel number')
    parser.add_argument('--index',type=int,default=0,help='Single-image index after filtering')
    parser.add_argument('--variable',help='Override measurement/CMI/CMI_Cnn')
    parser.add_argument('--mode',choices=['single','day','multi-day'],default='single')
    parser.add_argument('--day',help='UTC day for intra-day playback (YYYY-MM-DD)')
    parser.add_argument('--frames-per-day',type=int,choices=range(4,9),default=4)
    parser.add_argument('--show-invalid',action='store_true')
    parser.add_argument('--hide-zero',action='store_true')
    parser.add_argument('--output',help='PNG for single image, HTML for animation')
    args=parser.parse_args(argv)
    rows=view_index.inventory(args.location,args.source,args.start,args.end,None)
    # The band selects single-band CMIP product rows, or the displayed variable
    # in multiband stores; it never filters a multiband inventory out of the search.
    if args.source=='goes' and rows and all(r['band'] is not None for r in rows):
        rows=[r for r in rows if r['band']==args.band]
    if not rows:parser.error('No observations match the requested source and period.')
    if len({r['dataset'] for r in rows})>1:parser.error('Choose one product/region folder so this view does not mix datasets.')
    name=args.variable
    if name is None:
        with open_raw(rows[0]['path']) as ds:
            name='measurement' if 'measurement' in ds else 'CMI' if 'CMI' in ds else f'CMI_C{args.band:02d}'
    if args.mode=='single':
        if not 0<=args.index<len(rows):parser.error(f'Index must select one of {len(rows)} observations.')
        output=args.output or 'figures/dataset-view.png'
        print(draw(rows[args.index]['path'],name,not args.show_invalid,args.hide_zero,output,display=False))
    else:
        if args.mode=='day':
            day=args.day or rows[0]['time'][:10];rows=[r for r in rows if r['time'][:10]==day]
        else:rows=view_index.daily_sample(rows,args.frames_per_day)
        with ui.FetchProgress(len(rows),'Display frames') as progress:
            frames=view_frames.prepare(rows,name,not args.show_invalid,args.hide_zero,progress=progress)
        print(view_frames.save_animation(frames,args.output or 'figures/dataset-animation.html'))
    return 0
