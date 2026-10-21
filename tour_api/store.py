from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
from .domain import now_iso


class Store:
    def __init__(self, path, max_jobs=1000, retention_seconds=86400):
        self.path = Path(path)
        self.max_jobs = max(1,max_jobs)
        self.retention_seconds = max(1,retention_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    name TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    last_error TEXT, last_attempt TEXT);
                CREATE TABLE IF NOT EXISTS errors (
                    name TEXT PRIMARY KEY, message TEXT NOT NULL, attempted_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_snapshot(self, name, items):
        if not items:
            raise ValueError('Пустой сбор не заменяет предыдущий снимок')
        timestamp = now_iso()
        payload = json.dumps(items, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute('''INSERT INTO snapshots VALUES(?,?,?,NULL,?)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload,
                fetched_at=excluded.fetched_at,last_error=NULL,last_attempt=excluded.last_attempt''',
                (name, payload, timestamp, timestamp))
            db.execute('DELETE FROM errors WHERE name=?', (name,))

    def record_error(self, name, message):
        timestamp = now_iso()
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO errors VALUES(?,?,?)', (name, str(message)[:1000], timestamp))
            db.execute('UPDATE snapshots SET last_error=?, last_attempt=? WHERE name=?', (str(message)[:1000], timestamp, name))

    def errors(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM errors')]

    def snapshot(self, name):
        with self.connect() as db:
            row = db.execute('SELECT * FROM snapshots WHERE name=?', (name,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data['items'] = json.loads(data.pop('payload'))
        return data

    def save_job(self, job_id, payload):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?)',
                       (job_id, json.dumps(payload, ensure_ascii=False, allow_nan=False), now_iso()))
            self._prune_jobs(db)

    def _prune_jobs(self, db):
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=self.retention_seconds)).isoformat(timespec='seconds')
        terminal="json_extract(payload,'$.status') IN ('complete','failed')"
        db.execute(f'DELETE FROM jobs WHERE {terminal} AND updated_at < ?', (cutoff,))
        db.execute(f'''DELETE FROM jobs WHERE id IN (
            SELECT id FROM jobs WHERE {terminal}
            ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?)''', (self.max_jobs,))

    def prune_jobs(self):
        with self.connect() as db:
            self._prune_jobs(db)

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row['payload']) if row else None

    def interrupt_pending_jobs(self):
        with self.connect() as db:
            self._prune_jobs(db)
            rows = db.execute("SELECT id,payload FROM jobs WHERE json_extract(payload,'$.status') IN ('queued','running')").fetchall()
            for row in rows:
                payload = json.loads(row['payload'])
                if payload.get('status') in ('queued', 'running'):
                    payload.update(status='failed', error='Сервис перезапущен; повторите поиск')
                    db.execute('UPDATE jobs SET payload=?,updated_at=? WHERE id=?',
                               (json.dumps(payload,ensure_ascii=False), now_iso(), row['id']))
            self._prune_jobs(db)
