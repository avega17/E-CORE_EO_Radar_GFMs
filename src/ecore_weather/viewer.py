"""Browse a local archive, HF prefix, run report, or individual raw Zarr subset."""
import argparse
import json
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
    band=w.Dropdown(options=[(f'C{i:02d}',i) for i in range(1,17)],value=13,description='Band',disabled=True,
                    tooltip='One satellite band per view or animation. Only stored bands can be displayed.')
    find=w.Button(description='Find observations',button_style='primary')
    dataset=w.Dropdown(options=[],description='Dataset',layout=w.Layout(width='95%'),
                      tooltip='Choose one product/satellite/region. Observation times use the slider below, not this menu.')
    status=w.HTML();errors=w.Output();state={'records':[],'selected':[]}
    quality=w.Checkbox(value=True,description='Hide invalid pixels',tooltip='Apply documented quality masks to display copies only.')
    zero=w.Checkbox(value=True,description='Hide zero values',tooltip='Make valid zero rainfall transparent for display. Stored zeros remain unchanged.')
    index=w.IntSlider(min=0,max=0,value=0,description='Observation',continuous_update=False)
    stamp=w.HTML('Find observations to select an image.')
    save=w.Checkbox(value=False,description='Save a PNG too')
    png_path=w.Text(value='figures/dataset-view.png',description='PNG path')
    single_button=w.Button(description='Show interactive map');single_output=w.Output()
    day=w.DatePicker(value=start_day.value,description='Day UTC',tooltip='One day within the search bounds; every available observation is included.')
    daily_button=w.Button(description='Prepare day animation');daily_output=w.Output()
    per_day=w.IntSlider(value=4,min=4,max=8,description='Frames/day',continuous_update=False,
                       tooltip='Evenly select 4–8 available observations per day; no temporal interpolation or gap filling.')
    multi_button=w.Button(description='Prepare multi-day animation');multi_output=w.Output()
    tabs=w.Tab(children=[w.VBox([index,stamp,save,png_path,single_button,single_output]),
                         w.VBox([day,w.HTML('All available samples on this day within the search bounds. Prepare once, then use Play or drag the frame slider.'),daily_button,daily_output]),
                         w.VBox([per_day,w.HTML('Use the start/end bounds above. Each day contributes available samples; missing days remain absent. Colors stay fixed across the sequence.'),multi_button,multi_output])])
    for i,label in enumerate(['Single image','Within one day','Across days']):tabs.set_title(i,label)

    def bounds():
        if not start_day.value or not end_day.value:raise ValueError('Choose start and end dates.')
        return (datetime.combine(start_day.value,start_time.value or time(0),tzinfo=timezone.utc),
                datetime.combine(end_day.value,end_time.value or time(0),tzinfo=timezone.utc))

    def change_storage(change):
        try:location.value=destination_root('hf') if change['new']=='Hugging Face' else '/mnt/p/ecore_eo_datasets'
        except ValueError as error:status.value=html.escape(str(error))
    storage_kind.observe(change_storage,names='value')
    def clear_search(change=None):
        state['records']=[];state['selected']=[];dataset.options=[];index.max=0;stamp.value='Parameters changed: click Find observations again.'
        for out in (single_output,daily_output,multi_output):out.clear_output()
        band.disabled=source.value=='mrms'
    for control in (source,location,start_day,end_day,start_time,end_time,band):control.observe(clear_search,names='value')

    def update_stamp(change=None):
        rows=state['selected']
        stamp.value=html.escape(f"{rows[index.value]['time']} · {index.value+1}/{len(rows)}") if rows else 'No observations selected.'
    index.observe(update_stamp,names='value')
    def choose_dataset(change):
        rows=[r for r in state['records'] if r['dataset']==change['new']]
        state['selected']=rows;index.max=max(0,len(rows)-1);index.value=0;update_stamp()
    dataset.observe(choose_dataset,names='value')

    def search(_):
        with errors:
            errors.clear_output(wait=True);find.disabled=True
            try:
                start,end=bounds()
                rows=view_index.inventory(location.value,source.value,start,end,band.value if source.value=='goes' else None)
                state['records']=rows
                groups=sorted({r['dataset'] for r in rows})
                dataset.options=[(g.split('/'+source.value+'/')[-1],g) for g in groups]
                choose_dataset({'new':dataset.value})
                day.value=start_day.value
                status.value=f'{len(rows):,} observations in {len(groups)} dataset(s). Choose a dataset, then a view.'
                if not rows:status.value+=' No matches: check the dates, source, band and location.'
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:find.disabled=False
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
    def render_single(_):
        with single_output:
            single_output.clear_output(wait=True);single_button.disabled=True
            try:
                rows=state['selected'];chosen=[rows[index.value]] if rows else []
                frames,name=prepare(chosen,768)
                display(view_frames.leaflet(frames))
                if save.value:print(draw(chosen[0]['path'],name,quality.value,zero.value,png_path.value,display=False))
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
                frames,_=prepare(rows,384);display(view_frames.leaflet(frames))
                print(f'{len(frames)} frames prepared. Playback reads no additional Zarr data. Display limit: 300 frames.')
            except Exception as error:print(f'{type(error).__name__}: {error}')
            finally:button.disabled=False
    daily_button.on_click(lambda _:animate('day'));multi_button.on_click(lambda _:animate('multi'))
    panel=w.VBox([source,storage_kind,location,help_text,w.HBox([start_day,start_time]),w.HBox([end_day,end_time]),
                  band,find,status,dataset,w.HBox([quality,zero]),errors,tabs])
    panel._ecore_controls={'source':source,'location':location,'start':start_day,'end':end_day,'start_time':start_time,
                          'end_time':end_time,'tabs':tabs,'search':find,'dataset':dataset,'band':band,'single':single_button,
                          'day':daily_button,'multi':multi_button,'state':state,'index':index}
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
    rows=view_index.inventory(args.location,args.source,args.start,args.end,args.band if args.source=='goes' else None)
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
