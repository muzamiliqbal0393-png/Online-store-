#!/usr/bin/env python3
"""Create a consistent SQLite backup. Treat output as sensitive personal data."""
import sqlite3,sys,os
from pathlib import Path
source=Path(__file__).resolve().parent/'data'/'store.sqlite3'
if len(sys.argv)!=2:raise SystemExit('Usage: python backup.py /path/to/backup.sqlite3')
target=Path(sys.argv[1]).resolve()
if target==source:raise SystemExit('Backup target cannot be the live database')
if target.exists():raise SystemExit('Target already exists; choose a new backup filename')
if not source.exists():raise SystemExit('No store database found')
target.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(source) as src,sqlite3.connect(target) as dst:src.backup(dst)
os.chmod(target,0o600)
print('Backup created:',target)
