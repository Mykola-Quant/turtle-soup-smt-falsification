"""
convert_histdata.py  --  HistData M1 ASCII -> clean UTC parquet.

Optional year filtering lets you carve out a SPECIFIC period without mixing it
with data already analysed (e.g. build a clean 2020-2024 out-of-sample set that
the 2025 analysis never touched).

HistData yearly files:   DAT_ASCII_XAUUSD_M1_2020.csv
HistData monthly files:  DAT_ASCII_XAUUSD_M1_202003.csv
The year is the first 4 digits of the token after "M1_".

Usage:
    python convert_histdata.py XAUUSD xauusd_1m.parquet
    python convert_histdata.py XAUUSD xauusd_2020_2024.parquet 2020 2024
"""

from __future__ import annotations
import pandas as pd
import glob
import os
import re
import sys


def file_year(path: str) -> int | None:
    m = re.search(r"_M1_(\d{4})", os.path.basename(path))
    return int(m.group(1)) if m else None


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: convert_histdata.py SYMBOL OUT [start_year end_year]")
    symbol = sys.argv[1]
    out = sys.argv[2]
    start_year = int(sys.argv[3]) if len(sys.argv) > 3 else None
    end_year = int(sys.argv[4]) if len(sys.argv) > 4 else None

    files = sorted(glob.glob(f"DAT_ASCII_{symbol}_M1_*.csv"))
    if start_year is not None:
        kept = []
        for f in files:
            y = file_year(f)
            if y is None:
                continue
            if start_year <= y <= (end_year if end_year is not None else 9999):
                kept.append(f)
        files = kept

    assert files, (f"no files matched DAT_ASCII_{symbol}_M1_*.csv"
                   + (f" in years {start_year}-{end_year}" if start_year else ""))
    print(f"converting {len(files)} file(s): "
          f"{', '.join(os.path.basename(f) for f in files)}")

    frames = [pd.read_csv(f, sep=";", header=None,
              names=["dt", "open", "high", "low", "close", "vol"]) for f in files]
    df = pd.concat(frames)
    df["dt"] = pd.to_datetime(df["dt"], format="%Y%m%d %H%M%S")
    # HistData M1 is EST without DST = fixed UTC-5
    df = df.set_index("dt").tz_localize("Etc/GMT+5").tz_convert("UTC")
    df = df[~df.index.duplicated(keep="first")].sort_index()

    # if a year filter was given, hard-trim to be safe (handles stray rows)
    if start_year is not None:
        lo = pd.Timestamp(f"{start_year}-01-01", tz="UTC")
        hi = pd.Timestamp(f"{(end_year or start_year) + 1}-01-01", tz="UTC")
        df = df[(df.index >= lo) & (df.index < hi)]

    df[["open", "high", "low", "close"]].to_parquet(out)
    print(f"{symbol}: {len(df):,} rows  "
          f"{df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}  -> {out}")


if __name__ == "__main__":
    main()
