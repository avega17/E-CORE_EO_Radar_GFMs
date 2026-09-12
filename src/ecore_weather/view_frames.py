"""Bounded display frames for Leaflet and animations; never modify stored rasters."""
import base64
import io
from pathlib import Path

import numpy as np

from .storage import open_raw


def frame(path, variable=None, quality=True, hide_zero=False, pixels=384):
    import rioxarray
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from pyproj import Transformer
    from . import diagnostics, goes
    with open_raw(path) as ds:
        variable=variable or ('measurement' if 'measurement' in ds else goes.science_variables(ds)[0])
        if variable not in ds:
            raise ValueError(f'{variable} is not saved in this subset. Choose another band or dataset.')
        codes,physical=diagnostics.classify(ds,variable)
        arr=physical.astype('float32').copy(deep=True)
        if quality: arr.values[codes.values!=0]=np.nan
        if hide_zero: arr.values[arr.values==0]=np.nan
        bbox=tuple(ds.attrs.get('requested_bbox',()))
        if variable=='measurement':
            arr=arr.assign_coords(longitude=(arr.longitude+180)%360-180).rename(longitude='x',latitude='y')
            arr=arr.rio.write_crs('EPSG:4326')
            if not bbox:bbox=(float(arr.x.min()),float(arr.y.min()),float(arr.x.max()),float(arr.y.max()))
        else:
            crs,height=goes.projection(ds)
            arr=arr.assign_coords(x=goes._physical_coordinate(ds.x)*height,y=goes._physical_coordinate(ds.y)*height).rio.write_crs(crs)
            if not bbox:
                lon,lat=goes.lonlat(ds);bbox=(float(np.nanmin(lon)),float(np.nanmin(lat)),float(np.nanmax(lon)),float(np.nanmax(lat)))
        west,south,east,north=bbox
        if not (-85<south<north<85):raise ValueError('The interactive map supports regions within Web Mercator latitude limits.')
        transform=Transformer.from_crs('EPSG:4326','EPSG:3857',always_xy=True)
        left,bottom=transform.transform(west,south);right,top=transform.transform(east,north)
        projected=arr.rio.write_nodata(np.nan).rio.reproject('EPSG:3857',shape=(pixels,pixels),
                    transform=from_bounds(left,bottom,right,top,pixels,pixels),resampling=Resampling.nearest)
        label=variable if variable!='CMI' else f'CMI C{int(ds.band_id.values.item()):02d}'
        return {'values':projected.values,'bbox':bbox,'extent':(left,right,bottom,top),
                'time':ds.attrs.get('observation_time',ds.attrs.get('time_coverage_start','')),
                'label':label,'units':physical.attrs.get('units',''),'path':str(path)}


def limits(frames):
    lows=[];highs=[]
    for f in frames:
        valid=f['values'][np.isfinite(f['values'])]
        if valid.size:lows.append(float(valid.min()));highs.append(float(valid.max()))
    low=min(lows) if lows else 0.;high=max(highs) if highs else 1.
    return low, high if high>low else low+1


def png(frame, scale):
    import matplotlib
    from matplotlib.colors import Normalize
    from PIL import Image
    rgba=matplotlib.colormaps['viridis'](Normalize(*scale,clip=True)(frame['values']),bytes=True)
    rgba[~np.isfinite(frame['values']),3]=0
    stream=io.BytesIO();Image.fromarray(rgba).save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


def prepare(records, variable=None, quality=True, hide_zero=False, pixels=384, progress=None, max_frames=1500):
    if not records:raise ValueError('No observations match this view.')
    if len(records)>max_frames:raise ValueError(f'{len(records)} frames exceed the {max_frames}-frame display limit. Shorten the period or reduce images per day.')
    frames=[]
    for i,row in enumerate(records):
        f=frame(row['path'],variable,quality,hide_zero,pixels)
        f['time']=row['time'];frames.append(f)
        if progress:progress(i+1,len(records),{'status':'prepared'})
    if len({tuple(f['bbox']) for f in frames})!=1:raise ValueError('Choose one dataset/region for an animation.')
    return frames


def leaflet(frames):
    """Preload PNGs once; Play changes the overlay without reading Zarr again."""
    import html
    import ipywidgets as w
    from ipyleaflet import Map, ImageOverlay, LayersControl, basemaps
    scale=limits(frames);urls=[png(f,scale) for f in frames]
    west,south,east,north=frames[0]['bbox']
    bounds=((south,west),(north,east))
    m=Map(center=((south+north)/2,(west+east)/2),zoom=7,scroll_wheel_zoom=True,
          basemap=basemaps.OpenStreetMap.Mapnik,layout=w.Layout(height='500px'))
    overlay=ImageOverlay(url=urls[0],bounds=bounds,name=frames[0]['label'],opacity=.8)
    m.add(overlay);m.add(LayersControl(position='topright'));m.fit_bounds(bounds)
    title=w.HTML();slider=w.IntSlider(min=0,max=len(frames)-1,value=0,description='Frame',continuous_update=False)
    play=w.Play(min=0,max=len(frames)-1,value=0,interval=500,disabled=len(frames)==1)
    link=w.jslink((play,'value'),(slider,'value'))
    def update(change):
        i=change['new'];overlay.url=urls[i]
        title.value=f"<b>{html.escape(frames[i]['label'])}</b> · {html.escape(frames[i]['time'])} UTC · {i+1}/{len(frames)}"
    slider.observe(update,names='value');update({'new':0})
    legend=w.HTML(f"<small>Fixed scale for all frames: {scale[0]:.3g}–{scale[1]:.3g} {html.escape(str(frames[0]['units']))}. "
                  "Transparent pixels are hidden for display. Basemap © OpenStreetMap contributors.</small>"
                  "<div style='width:240px;height:10px;background:linear-gradient(to right,#440154,#3b528b,#21918c,#5ec962,#fde725)'></div>")
    panel=w.VBox([title,m,w.HBox([play,slider]),legend])
    # Keep the link and preloaded frames reachable while this panel is displayed.
    panel._ecore_link=link
    panel._ecore_frames=frames
    return panel


def ffmpeg_available():
    import shutil
    return shutil.which('ffmpeg') is not None


ANIMATION_FORMATS = ('html', 'gif', 'mp4')


def save_animation(frames, path, interval=500):
    """Export prepared frames; the writer is chosen from the file extension.

    .html writes a self-contained jshtml page (no tile service), .gif uses the
    Pillow writer, and .mp4 uses ffmpeg when a binary is available. Colors use
    the same fixed scale as the interactive view.
    """
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter, FFMpegWriter
    scale=limits(frames)
    fig,ax=plt.subplots(figsize=(7,6),constrained_layout=True)
    artist=ax.imshow(frames[0]['values'],extent=frames[0]['extent'],vmin=scale[0],vmax=scale[1],cmap='viridis')
    ax.set(xlabel='Web Mercator easting (m)',ylabel='Web Mercator northing (m)')
    fig.colorbar(artist,ax=ax,label=f"{frames[0]['label']} ({frames[0]['units']})")
    def update(i):artist.set_data(frames[i]['values']);ax.set_title(frames[i]['time']);return [artist]
    animation=FuncAnimation(fig,update,frames=len(frames),interval=interval,blit=False)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    suffix=path.suffix.lower().lstrip('.')
    try:
        import matplotlib as mpl
        if suffix in ('html','htm'):
            with mpl.rc_context({'animation.embed_limit':100}):path.write_text(animation.to_jshtml())
        elif suffix=='gif':
            animation.save(path,writer=PillowWriter(fps=max(1,round(1000/interval))))
        elif suffix=='mp4':
            if not ffmpeg_available():raise RuntimeError('ffmpeg is not installed; choose .html or .gif.')
            animation.save(path,writer=FFMpegWriter(fps=max(1,round(1000/interval))))
        else:
            raise ValueError('Choose a .html, .gif, or .mp4 export path.')
    finally:plt.close(fig)
    return str(path)
