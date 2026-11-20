from contextlib import asynccontextmanager
import asyncio
import json
import logging
from pathlib import Path
import queue
import threading
import time
import uuid

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from PIL import Image, ImageOps, UnidentifiedImageError
from . import store
from .inference import CpuReporter, Settings
from .risk import classify, quality, priority

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
jobs = queue.Queue()
stop = threading.Event()
state = {'status': 'loading', 'model': 'Qwen3.5-2B', 'error': None}
engine = None
logger = logging.getLogger('uvicorn.error')


def process_batch(batch_id):
    with store.db() as con:
        changed = con.execute("UPDATE batches SET status='running',started=? WHERE id=? AND status='queued'", (time.time(), batch_id)).rowcount
        if not changed:
            return  # A queued batch may have been paused before the worker reached it.
        rows = con.execute("SELECT * FROM images WHERE batch_id=? AND status='pending' ORDER BY name", (batch_id,)).fetchall()
    for row in rows:
        if stop.is_set():
            break
        with store.db() as con:
            if con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0] == 'pausing':
                break
        tick = time.perf_counter()
        metrics = {}
        try:
            metrics = quality(row['path'])
            gate = priority(metrics)
            signature = engine.settings.signature()
            if gate['action'] == 'skip_inference':
                result = dict(status='skipped_quality', report=None, attempts=[], seconds=0, error='; '.join(gate['reasons']))
            else:
                result = engine.analyse(row['path'])
            result['provenance'] = {'kind': 'fresh_inference' if gate['action'] != 'skip_inference' else 'quality_gate', 'signature': signature, 'sha256': metrics['sha256']}
            result['quality'] = metrics
            result['risk'] = classify(result.get('report'), metrics, status='error' if result.get('status') == 'error' else 'done', notes=result.get('observation_notes'))
            result.setdefault('processing_status', 'completed' if result.get('report') else 'failed')
            status = 'done'
            if result.get('status') == 'error' or (result.get('status') == 'generated' and not result.get('report')):
                result['error'] = 'No usable model observations were returned. Manual review needed.'
                status = 'error'
        except Exception as exc:
            if stop.is_set():
                break
            logger.exception('Image analysis failed: %s', row['name'])
            result = {'error': str(exc), 'processing_status': 'failed', 'risk': classify(quality=metrics, status='error')}
            status = 'error'
        with store.db() as con:
            con.execute('UPDATE images SET status=?,result=?,seconds=? WHERE id=?', (status, json.dumps(result), time.perf_counter() - tick, row['id']))
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        current = con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM images WHERE batch_id=? AND status='pending'", (batch_id,)).fetchone()[0]
        status = 'finished' if not pending else ('paused' if current == 'pausing' else 'interrupted')
        con.execute("UPDATE batches SET status=?,elapsed=elapsed+MAX(0,?-COALESCE(started,?)),started=NULL WHERE id=?", (status, time.time(), time.time(), batch_id))


def load_engine():
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    for key in ('model', 'projector', 'server'):
        if config.get(key) and not Path(config[key]).is_absolute():
            config[key] = str(ROOT / config[key])
    from prepare_models import verify_testing_artifacts
    verify_testing_artifacts(config)
    return CpuReporter(Settings(**config), cancelled=stop.is_set)


def worker():
    global engine
    try:
        engine = load_engine()
        state['model_path'] = engine.settings.model
        state['projector_path'] = engine.settings.projector
        state['status'] = 'ready'
    except Exception as exc:
        state.update(status='error', error=str(exc))
        logger.exception('Model startup failed')
        return
    try:
        while not stop.is_set():
            try:
                batch_id = jobs.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                process_batch(batch_id)
            except Exception:
                logger.exception('Batch interrupted: %s', batch_id)
                with store.db() as con:
                    con.execute("UPDATE batches SET status='interrupted',elapsed=elapsed+MAX(0,?-started),started=NULL WHERE id=?", (time.time(), batch_id))
            finally:
                jobs.task_done()
    finally:
        engine.close()


@asynccontextmanager
async def lifespan(app):
    global engine
    engine = None
    state.update(status='loading', error=None)
    store.init()
    stop.clear()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    yield
    stop.set()
    if engine:
        engine.close()
    await asyncio.to_thread(thread.join, 8)


app = FastAPI(title='Embsys Jewellery Inspection', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_origin(request, call_next):
    from fastapi.responses import JSONResponse
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}' and request.method != 'GET':
        return JSONResponse({'detail': 'Use the local application to make this request.'}, status_code=403)
    return await call_next(request)


@app.get('/api/health')
def health():
    return state


@app.post('/api/shutdown')
def shutdown(background_tasks: BackgroundTasks):
    callback = getattr(app.state, 'shutdown_callback', None)
    if callback is None:
        raise HTTPException(503, 'Shutdown is available when the application is started with Start.bat or launch.py.')
    state['status'] = 'stopping'
    stop.set()
    background_tasks.add_task(callback)
    return {'status': 'stopping'}


@app.get('/api/batches')
def batches():
    with store.db() as con:
        ids = [row[0] for row in con.execute("SELECT id FROM batches WHERE status!='deleted' ORDER BY created DESC")]
    return [store.batch(batch_id) for batch_id in ids]


@app.post('/api/batches')
def create_batch(body: dict):
    name = str(body.get('name', 'Image batch')).strip()[:180] or 'Image batch'
    batch_id = uuid.uuid4().hex
    with store.db() as con:
        con.execute('INSERT INTO batches(id,name,status,created) VALUES(?,?,?,?)', (batch_id, name, 'uploading', time.time()))
    (store.DATA / batch_id).mkdir()
    return store.batch(batch_id)


def require_batch(batch_id):
    batch = store.batch(batch_id)
    if not batch or batch['status'] == 'deleted':
        raise HTTPException(404, 'Batch not found')
    return batch


@app.post('/api/batches/{batch_id}/images')
async def upload(batch_id: str, files: list[UploadFile] = File(...)):
    batch = require_batch(batch_id)
    if batch['status'] != 'uploading':
        raise HTTPException(409, 'This batch is already loaded')
    accepted, rejected = [], []
    for file in files:
        name = (file.filename or 'image').replace('\\', '/')[:500]
        suffix = Path(name).suffix.lower()
        if suffix not in EXTENSIONS:
            rejected.append({'name': name, 'reason': 'Unsupported image format'})
            await file.close()
            continue
        image_id = uuid.uuid4().hex
        target = store.DATA / batch_id / (image_id + suffix)
        try:
            size = 0
            with target.open('wb') as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 40 * 1024 * 1024:
                        raise ValueError('Image exceeds 40 MB')
                    output.write(chunk)
            try:
                with Image.open(target) as image:
                    image.verify()
                status, result = 'pending', None
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
                status = 'error'
                result = json.dumps({'error': 'Unreadable image: ' + str(exc), 'quality': quality(target), 'risk': classify(quality=quality(target), status='error')})
            with store.db() as con:
                # Recheck under the same transaction as insertion.
                con.execute('BEGIN IMMEDIATE')
                if con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0] != 'uploading':
                    raise ValueError('Batch is no longer accepting images')
                con.execute('INSERT INTO images(id,batch_id,name,path,status,result) VALUES(?,?,?,?,?,?)', (image_id, batch_id, name, str(target), status, result))
            accepted.append(image_id)
        except Exception as exc:
            target.unlink(missing_ok=True)
            rejected.append({'name': name, 'reason': str(exc)})
        finally:
            await file.close()
    return {'accepted': accepted, 'rejected': rejected}


@app.post('/api/batches/{batch_id}/finish-upload')
def finish_upload(batch_id: str):
    batch = require_batch(batch_id)
    if not batch['total']:
        raise HTTPException(400, 'Choose a folder containing supported images')
    with store.db() as con:
        changed = con.execute("UPDATE batches SET status='ready' WHERE id=? AND status='uploading'", (batch_id,)).rowcount
    if not changed:
        raise HTTPException(409, 'Batch is already loaded')
    return store.batch(batch_id)


@app.post('/api/batches/{batch_id}/run')
def run_batch(batch_id: str):
    require_batch(batch_id)
    if state['status'] != 'ready':
        raise HTTPException(503, state['error'] or 'Model is loading')
    with store.db() as con:
        changed = con.execute("UPDATE batches SET status='queued' WHERE id=? AND status IN ('ready','interrupted')", (batch_id,)).rowcount
    if not changed:
        raise HTTPException(409, 'Batch is already queued, running, or finished')
    jobs.put(batch_id)
    return store.batch(batch_id)


@app.get('/api/batches/{batch_id}')
def detail(batch_id: str):
    batch = require_batch(batch_id)
    return {**batch, 'images': store.images(batch_id)}


@app.post('/api/batches/{batch_id}/pause')
def pause_batch(batch_id: str):
    require_batch(batch_id)
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        status = con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
        if status == 'queued':
            con.execute("UPDATE batches SET status='paused' WHERE id=?", (batch_id,))
        elif status == 'running':
            con.execute("UPDATE batches SET status='pausing' WHERE id=?", (batch_id,))
        elif status not in ('pausing', 'paused'):
            raise HTTPException(409, 'Only queued or processing batches can be paused')
    return store.batch(batch_id)


@app.post('/api/batches/{batch_id}/continue')
def continue_batch(batch_id: str):
    require_batch(batch_id)
    if state['status'] != 'ready':
        raise HTTPException(503, state['error'] or 'Model is loading')
    with store.db() as con:
        changed = con.execute("UPDATE batches SET status='queued' WHERE id=? AND status='paused'", (batch_id,)).rowcount
    if not changed:
        raise HTTPException(409, 'Wait until the batch is paused before continuing')
    jobs.put(batch_id)
    return store.batch(batch_id)


def archive_batch(batch_id, con, operation):
    folder = store.DATA / 'history' / batch_id
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = dict(operation=operation, archived_at=time.time(),
                    batch=dict(con.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()),
                    images=[dict(row) for row in con.execute('SELECT * FROM images WHERE batch_id=?', (batch_id,))])
    (folder / (uuid.uuid4().hex + '.json')).write_text(json.dumps(snapshot, indent=2), encoding='utf-8')


@app.post('/api/batches/{batch_id}/rerun')
def rerun_batch(batch_id: str):
    require_batch(batch_id)
    if state['status'] != 'ready':
        raise HTTPException(503, state['error'] or 'Model is loading')
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        status = con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
        if status not in ('finished', 'interrupted', 'ready', 'paused'):
            raise HTTPException(409, 'Pause this batch or wait until it finishes before rerunning it')
        archive_batch(batch_id, con, 'rerun')
        con.execute("UPDATE images SET status='pending',result=NULL,seconds=0 WHERE batch_id=?", (batch_id,))
        con.execute("UPDATE batches SET status='queued',elapsed=0,started=NULL WHERE id=?", (batch_id,))
    jobs.put(batch_id)
    return store.batch(batch_id)


@app.delete('/api/batches/{batch_id}')
def delete_batch(batch_id: str):
    require_batch(batch_id)
    with store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        status = con.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
        if status in ('queued', 'running', 'pausing'):
            raise HTTPException(409, 'Wait until this batch finishes before deleting it')
        if status == 'deleted':
            raise HTTPException(404, 'Batch not found')
        archive_batch(batch_id, con, 'delete')
        con.execute("UPDATE batches SET status='deleted' WHERE id=?", (batch_id,))
    return {'deleted': True}


@app.get('/api/images/{image_id}')
def image_file(image_id: str, thumbnail: bool = False):
    with store.db() as con:
        row = con.execute("SELECT images.path FROM images JOIN batches ON batches.id=images.batch_id WHERE images.id=? AND batches.status!='deleted'", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Image not found')
    path = Path(row['path'])
    if thumbnail:
        thumb = path.with_suffix('.thumb.jpg')
        if not thumb.exists():
            try:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    image.thumbnail((480, 360))
                    image.save(thumb, quality=85)
            except Exception:
                raise HTTPException(422, 'Preview unavailable')
        path = thumb
    return FileResponse(path, headers={'Cache-Control': 'private, max-age=86400'})


@app.get('/api/batches/{batch_id}/export')
def export_batch(batch_id: str):
    from fastapi.responses import Response
    batch = require_batch(batch_id)
    return Response(json.dumps({**batch, 'model': state['model'], 'images': store.images(batch_id)}, indent=2), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="batch-{batch_id[:8]}.json"'})


DIST = ROOT / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='frontend')
