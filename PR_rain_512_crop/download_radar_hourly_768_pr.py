"""
download_radar_hourly_768_pr.py

Description:
    Downloads, aligns, filters, and saves a single hour of NOAA MRMS CARIB QPE 
    data to a 768x768 parent matrix grid matching the extended Puerto Rico domain.
    
    The 768x768 canvas provides a 256x256 pixel translation margin, enabling 
    extensive random 512x512 cropping for spatial data augmentation.
"""

import os
import glob
import gzip
import shutil
import s3fs
import xarray as xr
import rioxarray
import numpy as np
from datetime import datetime

from rasterio.transform import from_origin
from rasterio.enums import Resampling

# =========================================================================
# GEOGRAPHIC GRID SETUP (Puerto Rico Center-Out Grid: 768x768 & 512x512)
# =========================================================================
# Standard geographic center anchor for Puerto Rico domain
CENTER_LAT, CENTER_LON = 18.2, -66.4

# EPSG:4326 WGS84 standard geographic spacing (~1.11 km/pixel)
RESOLUTION_DEG = 0.01                 

# -------------------------------------------------------------------------
# 1. Parent Canvas Configuration (768x768)
# -------------------------------------------------------------------------
GRID_SIZE_PARENT = 768                      
HALF_SPAN_PARENT = (GRID_SIZE_PARENT * RESOLUTION_DEG) / 2.0  # 3.84°

WEST_768  = CENTER_LON - HALF_SPAN_PARENT   # -66.40 - 3.84 = -70.24°
EAST_768  = CENTER_LON + HALF_SPAN_PARENT   # -66.40 + 3.84 = -62.56°
SOUTH_768 = CENTER_LAT - HALF_SPAN_PARENT   #  18.20 - 3.84 =  14.36°
NORTH_768 = CENTER_LAT + HALF_SPAN_PARENT   #  18.20 + 3.84 =  22.04°

EXTENT_768 = [WEST_768, EAST_768, SOUTH_768, NORTH_768]

# -------------------------------------------------------------------------
# 2. Centered Target Crop Reference Configuration (512x512)
# -------------------------------------------------------------------------
GRID_SIZE_CROP = 512                      
HALF_SPAN_CROP = (GRID_SIZE_CROP * RESOLUTION_DEG) / 2.0    # 2.56°

WEST_512  = CENTER_LON - HALF_SPAN_CROP     # -66.40 - 2.56 = -68.96°
EAST_512  = CENTER_LON + HALF_SPAN_CROP     # -66.40 + 2.56 = -63.84°
SOUTH_512 = CENTER_LAT - HALF_SPAN_CROP     #  18.20 - 2.56 =  15.64°
NORTH_512 = CENTER_LAT + HALF_SPAN_CROP     #  18.20 + 2.56 =  20.76°

EXTENT_512_CENTERED = [WEST_512, EAST_512, SOUTH_512, NORTH_512]


def fetch_mrms_radar_hour_768(target_datetime, output_dir="radar_dataset_768"):
    """
    Downloads, aligns, filters, and saves a single hour of NOAA MRMS CARIB QPE 
    data to a 768x768 square matrix grid matching the extended Puerto Rico footprint.
    """
    # Build Rasterio affine transform for top-left WGS84 origin (West, North)
    master_transform = from_origin(WEST_768, NORTH_768, RESOLUTION_DEG, RESOLUTION_DEG)

    date_str = target_datetime.strftime("%Y%m%d")
    time_str = target_datetime.strftime("%H0000") 
    
    year_str = target_datetime.strftime("%Y")
    month_str = target_datetime.strftime("%m")
    day_str = target_datetime.strftime("%d")
    hour_str = target_datetime.strftime("%H")  # Zero-padded 2-digit hour: 00 through 23
    
    # Standardized directory structure: YYYY/MM/DD/HH/
    final_output_dir = os.path.join(output_dir, year_str, month_str, day_str, hour_str)
    os.makedirs(final_output_dir, exist_ok=True)
    
    filename_base = f"pr_radar_768_{date_str}_{time_str}"
    output_tif = os.path.join(final_output_dir, f"{filename_base}.tif")

    # Remote S3 AWS NOAA MRMS path
    s3_path = f"noaa-mrms-pds/CARIB/MultiSensor_QPE_01H_Pass2_00.00/{date_str}/MRMS_MultiSensor_QPE_01H_Pass2_00.00_{date_str}-{time_str}.grib2.gz"

    local_gz = f"temp_{filename_base}.grib2.gz"
    local_grib = f"temp_{filename_base}.grib2"

    fs = s3fs.S3FileSystem(anon=True)
    
    if not fs.exists(s3_path):
        print(f"   [!] File missing on NOAA AWS Archive: {s3_path}")
        return False

    try:
        fs.get(s3_path, local_gz)

        with gzip.open(local_gz, 'rb') as f_in, open(local_grib, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

        # Open GRIB2 binary file using cfgrib engine
        ds = xr.open_dataset(local_grib, engine="cfgrib")
        
        # ds.data_vars lists internal dataset variables like dictionary keys.
        # list(...)[0] dynamically extracts the 1st variable key name
        # so internal NOAA variable naming updates won't break the code.
        radar_var_name = list(ds.data_vars)[0]
        
        # Query the dataset object with the key name to retrieve the 2D radar array
        radar_raw = ds[radar_var_name]

        # Enforce North-is-Up orientation if stored South-to-North
        if radar_raw.latitude[0].item() < radar_raw.latitude[-1].item():
            radar_raw = radar_raw.reindex(latitude=radar_raw.latitude[::-1])

        # CRS (Coordinate Reference System) defines how pixel coordinates map to the physical Earth.
        # EPSG:4326 is standard WGS84 Latitude/Longitude in degrees.
        radar_raw = radar_raw.rio.write_crs("EPSG:4326")
        
        # Reproject raw radar array onto the target 768x768 grid geometry
        radar_aligned = radar_raw.rio.reproject(
            "EPSG:4326", 
            shape=(GRID_SIZE_PARENT, GRID_SIZE_PARENT), 
            transform=master_transform, 
            resampling=Resampling.bilinear
        )
        
        # =========================================================================
        # TELEMETRY CLEANING & OUT-OF-BOUNDS MASKING PROTOCOL
        # =========================================================================
        # 1. Flag negative radar noise/telemetry (<0) and native IEEE NaNs
        is_missing = (radar_aligned < 0) | radar_aligned.isnull()
        
        # 2. Clamp floating point precision noise inside active domain to 0.0 mm/h
        radar_cleaned = radar_aligned.clip(min=0.0)
        
        # 3. Apply mask: Valid rain >= 0.0 mm/h inside domain, np.nan outside coverage
        radar_final = xr.where(is_missing, np.nan, radar_cleaned)
        
        # 4. Encode IEEE NaN as explicit NoData metadata tag for GeoTIFF
        radar_final = radar_final.rio.write_nodata(np.nan, encoded=True)
        radar_final.rio.to_raster(output_tif)
        
        print(f"      -> Success: Saved aligned 768x768 grid -> {output_tif}")
        return True

    except Exception as e:
        print(f"   [!] Processing error at timestamp {date_str}-{time_str}: {str(e)}")
        return False

    finally:
        # Remove temporary downloaded GRIB2 files
        for temp_file in [local_gz, local_grib]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
        
        # Remove cfgrib index files
        for idx_file in glob.glob("./temp*.idx"):
            try:
                os.remove(idx_file)
            except OSError:
                pass


if __name__ == "__main__":
    print("\n==============================================")
    print("   NOAA MRMS RADAR DOWNLOADER ENGINE (768x768)")
    print("==============================================")
    print("Press Enter directly at any prompt to accept default setup: 2021-01-01 00:00 UTC")
    print("==============================================")
    
    year_in  = input("Enter Year  (e.g., 2021, 2025)     [Default: 2021]: ").strip()
    year     = int(year_in) if year_in else 2021
    
    month_in = input("Enter Month (1 to 12)              [Default: 1]   : ").strip()
    month    = int(month_in) if month_in else 1
    
    day_in   = input("Enter Day   (1 to 31)              [Default: 1]   : ").strip()
    day      = int(day_in) if day_in else 1
    
    hour_in  = input("Enter Hour  (0 to 23 UTC)          [Default: 0]   : ").strip()
    hour     = int(hour_in) if hour_in != "" else 0
    
    try:
        target_dt = datetime(year, month, day, hour, 0, 0)
    except ValueError as e:
        print(f"\n[!] Invalid Calendar Parameters: {str(e)}")
        print("Resetting execution path to fallback standard: 2021-01-01 00:00:00 UTC")
        target_dt = datetime(2021, 1, 1, 0, 0, 0)

    print("\n==============================================")
    print("            EXECUTION OVERVIEW                ")
    print("==============================================")
    print(f"[*] Target Timestamp:      {target_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"[*] Grid Center Anchor:    ({CENTER_LAT:.2f}°, {CENTER_LON:.2f}°)")
    print(f"[*] Projection CRS:        EPSG:4326 (WGS84)")
    print(f"[*] Pixel Spacing Size:    {RESOLUTION_DEG}° (~1.11 km)")
    print(f"[*] Parent Canvas Grid:    {GRID_SIZE_PARENT}x{GRID_SIZE_PARENT}")
    print(f"[*] Parent Domain [W,E,S,N]: [{WEST_768:.2f}°, {EAST_768:.2f}°, {SOUTH_768:.2f}°, {NORTH_768:.2f}°]")
    print(f"[*] Center 512 Crop Extent: [{WEST_512:.2f}°, {EAST_512:.2f}°, {SOUTH_512:.2f}°, {NORTH_512:.2f}°]")
    print(f"[*] Target Directory:      radar_dataset_768/{target_dt.strftime('%Y/%m/%d/%H')}/")
    print("==============================================")
    
    success = fetch_mrms_radar_hour_768(target_dt)
    
    if success:
        print("=== HOURLY DATA RETRIEVAL PIPELINE SUCCESSFUL ===\n")
    else:
        print("=== PIPELINE DATA RETRIEVAL ENCOUNTERED AN ERROR ===\n")
