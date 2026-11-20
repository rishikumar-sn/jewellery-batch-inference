import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('AU_DATA_DIR', ROOT / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)


@contextmanager
def db():
    connection = sqlite3.connect(DATA / 'batches.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init():
    with db() as con:
        con.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT, status TEXT, created REAL, started REAL, elapsed REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, batch_id TEXT, name TEXT, path TEXT, status TEXT DEFAULT 'pending', result TEXT, seconds REAL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS images_batch ON images(batch_id);''')
        # Uploaded files travel with data/ when the project is moved to another PC.
        for row in con.execute('SELECT id,batch_id,path FROM images').fetchall():
            local = DATA / row['batch_id'] / Path(row['path']).name
            if local.is_file() and str(local) != row['path']:
                con.execute('UPDATE images SET path=? WHERE id=?', (str(local), row['id']))
        con.execute("UPDATE batches SET status='paused', elapsed=elapsed+MAX(0,?-COALESCE(started,?)), started=NULL WHERE status='pausing'", (time.time(), time.time()))
        con.execute("UPDATE batches SET status='interrupted', elapsed=elapsed+MAX(0,?-started), started=NULL WHERE status='running'", (time.time(),))
        con.execute("UPDATE batches SET status='interrupted' WHERE status='queued'")


def batch(batch_id):
    with db() as con:
        row = con.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        counts = con.execute('SELECT status,COUNT(*) n FROM images WHERE batch_id=? GROUP BY status', (batch_id,)).fetchall()
        counts = {row['status']: row['n'] for row in counts}
        result['total'] = sum(counts.values())
        result['done'] = counts.get('done', 0) + counts.get('error', 0)
        result['errors'] = counts.get('error', 0)
        result['elapsed'] += max(0, time.time() - result['started']) if result['started'] else 0
        durations = con.execute("SELECT AVG(seconds) FROM images WHERE batch_id=? AND status IN ('done','error') AND seconds>0", (batch_id,)).fetchone()[0]
        remaining = result['total'] - result['done']
        result['eta'] = max(0, durations * remaining) if durations and remaining and result['status'] == 'running' else None
        return result


def images(batch_id):
    from .risk import TIERS
    with db() as con:
        rows = [dict(row) for row in con.execute('SELECT * FROM images WHERE batch_id=?', (batch_id,))]
    for row in rows:
        row.pop('path')
        row['result'] = json.loads(row['result']) if row['result'] else None
    def order(row):
        risk = (row['result'] or {}).get('risk', {})
        # Technical failures remain prominent, immediately after critical cases.
        rank = TIERS.get(risk.get('tier'), 0)
        if rank < 4 and (risk.get('tier') == 'unassessed' or (row['result'] or {}).get('processing_status') in ('failed', 'needs_verification')):
            rank = 3.5
        return (-rank, -risk.get('score', -1), row['name'].lower())
    return sorted(rows, key=order)
