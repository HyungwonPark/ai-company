"""Fail closed before the legacy timer can invoke an unaccounted model call.

The timer may continue firing.  This replacement service only reads its queue;
any existing job remains untouched for a separately reviewed migration.
"""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def check(path: Path) -> int:
    if not path.is_file():
        print('legacy queue missing; no model call started')
        return 2
    try:
        uri = path.resolve(strict=True).as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('queue integrity failed')
            count = db.execute('SELECT count(*) FROM session_jobs').fetchone()[0]
    except (OSError, sqlite3.Error, ValueError):
        print('legacy queue unreadable; no model call started')
        return 2
    if count:
        print(f'legacy queue has {count} retained jobs; reviewed migration required; no model call started')
        return 2
    print('legacy queue empty; no model call started')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    raise SystemExit(check(parser.parse_args().database))
