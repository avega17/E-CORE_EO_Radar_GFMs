"""
download_radar_range_768_pr.py

Description:
    Batch range downloader engine for 768x768 Puerto Rico radar data.
    Provides modular functions for Year, Month, Day, and Hour downloads 
    for easy invocation via HPC batch scripts or interactive CLI menus.
"""

import calendar
import os
from datetime import datetime, timedelta

# Import the 768x768 hourly downloader function
from download_radar_hourly_768_pr import fetch_mrms_radar_hour_768


# =========================================================================
# CORE BATCH RANGE ENGINE
# =========================================================================
def download_radar_time_range_768(start_dt, end_dt):
    """
    Loops through every hour between start_dt and end_dt (inclusive)
    to download 768x768 radar data.
    """
    print("\n==============================================")
    print("       STARTING 768x768 BATCH DOWNLOAD        ")
    print("==============================================")
    print("[*] Time Window:")
    print(f"    First Hour: {start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"    Last Hour:  {end_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("==============================================")

    current_dt = start_dt
    success_count = 0
    fail_count = 0

    total_hours = int((end_dt - start_dt).total_seconds() / 3600) + 1
    current_index = 1
    log_file_path = "missing_hours.txt"

    while current_dt <= end_dt:
        print(
            f"[{current_index}/{total_hours}] Processing: {current_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        status = fetch_mrms_radar_hour_768(current_dt)

        if status:
            success_count += 1
        else:
            fail_count += 1
            with open(log_file_path, "a") as log_file:
                timestamp_str = current_dt.strftime("%Y-%m-%d %H:00:00")
                log_file.write(f"{timestamp_str}\n")

        current_dt += timedelta(hours=1)
        current_index += 1

    print("\n==============================================")
    print("      768x768 DOWNLOAD RANGE SUMMARY         ")
    print("==============================================")
    print(f" -> Successfully Downloaded: {success_count} / {total_hours} hours.")
    if fail_count > 0:
        print(f" -> Warning: {fail_count} files were missing or failed to download.")
        print(f"             Missing hours saved to: '{log_file_path}'")
    print("==============================================\n")


# =========================================================================
# STANDALONE MODULAR FUNCTIONS (HPC & EXTERNAL SCRIPT FRIENDLY)
# =========================================================================
def download_radar_year(start_year, end_year=None):
    """Downloads full calendar years (Jan 1 00:00 to Dec 31 23:00)."""
    if end_year is None:
        end_year = start_year
    start_dt = datetime(start_year, 1, 1, 0, 0, 0)
    end_dt = datetime(end_year, 12, 31, 23, 0, 0)
    download_radar_time_range_768(start_dt, end_dt)


def download_radar_month(year, start_month, end_month=None):
    """Downloads full calendar months (1st day 00:00 to last day 23:00)."""
    if end_month is None:
        end_month = start_month
    _, last_day = calendar.monthrange(year, end_month)
    start_dt = datetime(year, start_month, 1, 0, 0, 0)
    end_dt = datetime(year, end_month, last_day, 23, 0, 0)
    download_radar_time_range_768(start_dt, end_dt)


def download_radar_day(year, month, start_day, end_day=None):
    """Downloads complete day ranges (Start day 00:00 to End day 23:00)."""
    if end_day is None:
        end_day = start_day
    start_dt = datetime(year, month, start_day, 0, 0, 0)
    end_dt = datetime(year, month, end_day, 23, 0, 0)
    download_radar_time_range_768(start_dt, end_dt)


def download_radar_hour_range(year, month, day, start_hour, end_hour=None):
    """Downloads specific hourly windows on a single day (Start hour to End hour)."""
    if end_hour is None:
        end_hour = start_hour
    if end_hour < start_hour:
        print("\n[!] Error: End hour cannot be earlier than start hour on the same day.")
        print("    To download multi-day periods, use download_radar_day().")
        return
    start_dt = datetime(year, month, day, start_hour, 0, 0)
    end_dt = datetime(year, month, day, end_hour, 0, 0)
    download_radar_time_range_768(start_dt, end_dt)


# =========================================================================
# INTERACTIVE TERMINAL MENU
# =========================================================================
if __name__ == "__main__":
    print("\n==============================================")
    print("   NOAA MRMS RADAR BATCH RANGE DOWNLOADER (768)")
    print("==============================================")
    print("Select how you want to specify the time range:")
    print(" [1] By Year  (Download complete calendar years)")
    print(" [2] By Month (Download entire months)")
    print(" [3] By Day   (Download specific date ranges) [DEFAULT]")
    print(" [4] By Hour  (Download exact hour-to-hour windows on a single day)")
    print("----------------------------------------------")

    mode = input("Enter choice [1-4, Default: 3]: ").strip()
    if not mode or mode not in ["1", "2", "3", "4"]:
        mode = "3"

    # MODE 1: YEAR RANGE
    if mode == "1":
        print("\n--- Mode 1: Year Range ---")
        st_yr = input("Enter Start Year (e.g., 2021) [Default: 2021]: ").strip()
        end_yr = input("Enter End Year   (e.g., 2021) [Default: 2021]: ").strip()

        start_year = int(st_yr) if st_yr else 2021
        end_year = int(end_yr) if end_yr else start_year
        download_radar_year(start_year, end_year)

    # MODE 2: MONTH RANGE
    elif mode == "2":
        print("\n--- Mode 2: Month Range ---")
        yr_in = input("Enter Year         (e.g., 2021) [Default: 2021]: ").strip()
        st_mo = input("Enter Start Month  (1 to 12)    [Default: 1]   : ").strip()
        end_mo = input("Enter End Month    (1 to 12)    [Default: 1]   : ").strip()

        year = int(yr_in) if yr_in else 2021
        start_month = int(st_mo) if st_mo else 1
        end_month = int(end_mo) if end_mo else start_month
        download_radar_month(year, start_month, end_month)

    # MODE 3: DAY RANGE
    elif mode == "3":
        print("\n--- Mode 3: Day Range ---")
        yr_in = input("Enter Year        (e.g., 2021) [Default: 2021]: ").strip()
        mo_in = input("Enter Month       (1 to 12)    [Default: 1]   : ").strip()
        st_dy = input("Enter Start Day   (1 to 31)    [Default: 1]   : ").strip()
        end_dy = input("Enter End Day     (1 to 31)    [Default: 1]   : ").strip()

        year = int(yr_in) if yr_in else 2021
        month = int(mo_in) if mo_in else 1
        start_day = int(st_dy) if st_dy else 1
        end_day = int(end_dy) if end_dy else start_day
        download_radar_day(year, month, start_day, end_day)

    # MODE 4: HOUR RANGE
    elif mode == "4":
        print("\n--- Mode 4: Hour Range (Single Day Window) ---")
        yr_in = input("Enter Year        (e.g., 2021) [Default: 2021]: ").strip()
        mo_in = input("Enter Month       (1 to 12)    [Default: 1]   : ").strip()
        dy_in = input("Enter Day         (1 to 31)    [Default: 1]   : ").strip()
        st_hr = input("Enter Start Hour  (0 to 23 UTC) [Default: 0]   : ").strip()
        end_hr = input("Enter End Hour    (0 to 23 UTC) [Default: 0]   : ").strip()

        year = int(yr_in) if yr_in else 2021
        month = int(mo_in) if mo_in else 1
        day = int(dy_in) if dy_in else 1
        start_hour = int(st_hr) if st_hr != "" else 0
        end_hour = int(end_hr) if end_hr != "" else start_hour
        download_radar_hour_range(year, month, day, start_hour, end_hour)
