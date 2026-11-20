import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from backend import app as service, store
from backend.observations import expand
from backend.risk import classify
from backend.inference import Settings


class FakeModel:
    def __init__(self, *args, **kwargs):
        self.settings = type('Settings', (), {'model': 'isolated-test-model', 'projector': 'isolated-test-projector', 'signature': lambda self: 'isolated-test'})()
    def close(self): pass
    def analyse(self, path):
        return {'report': {'jewellery_presence': 'present', 'arrangement': 'well_separated', 'packet': {'placement': 'absent'}, 'visual_details': {'threads': 'present'}, 'scale': {'presence': 'present', 'weight_text': '12.34 g'}}, 'observation_notes': [], 'attempts': [{'raw': '{"threads":"present"}'}]}


class PriorityTests(unittest.TestCase):
    def test_missing_assessment_requires_technical_review(self):
        self.assertEqual(classify()['tier'], 'unassessed')
        risk = classify({'jewellery_presence': 'present'})
        self.assertEqual(risk['tier'], 'unassessed')
        self.assertIn('needs_review', classify()['categories'])

    def test_absent_and_conflicting_presence_never_low_risk(self):
        for report in ({'jewellery_presence': 'absent', 'jewel_count': 0},
                       {'jewellery_presence': 'unknown'},
                       {'jewellery_presence': 'present', 'jewel_count': 0},
                       {'description': 'An open packet'}):
            with self.subTest(report=report):
                risk = classify(report, {'automatic_metrics': {'focus_score': 635}})
                self.assertIn(risk['tier'], ('high', 'unassessed'))
                self.assertIn('needs_review', risk['categories'])
        absent = classify({'jewellery_presence': 'absent'})
        self.assertIn('jewellery_absent', absent['categories'])
        self.assertIn('No jewellery visible', absent['reasons'][0])

    def test_obscured_packaging_requires_review_and_keeps_optional_filters(self):
        report = {'jewellery_presence': 'present', 'arrangement': 'bundled', 'packet': {'placement': 'inside', 'visibility': 'obscured'}, 'visual_details': {'threads': 'present'}, 'scale': {'weight_text': '0.00 g'}}
        risk = classify(report)
        self.assertEqual(risk['tier'], 'high')
        self.assertTrue({'threads','weight_present','packet_inside','obscured','bundled'} <= set(risk['categories']))

    def test_decoder_retains_unknown_and_truncation(self):
        _, report, notes = expand('{"threads":"unknown","weight":"12.30 g","packet":"inside",', include_notes=True)
        self.assertEqual(report['visual_details']['threads'], 'unknown')
        self.assertEqual(report['scale']['weight_text'], '12.30 g')
        self.assertTrue(notes)
        self.assertNotIn('threads', classify(report, notes=notes)['categories'])

    def test_non_numeric_weight_stays_out_of_weight_filter(self):
        _, report, notes = expand('{"weight":"unreadable display"}', include_notes=True)
        self.assertNotIn('weight_present', classify(report, notes=notes)['categories'])
        self.assertEqual(report['scale']['weight_text'], 'unreadable display')

    def test_threads_and_notes_are_filters_not_severity(self):
        baseline = classify({'jewellery_presence': 'present'})
        findings = classify({'jewellery_presence': 'present', 'packet': {'placement': 'inside'}, 'visual_details': {'threads': 'present', 'black_beads': 'present', 'coloured_stones_or_beads': 'present'}}, notes=['Incomplete JSON'])
        self.assertEqual(findings['tier'], baseline['tier'])
        self.assertEqual(findings['score'], baseline['score'])
        self.assertIn('threads', findings['categories'])


class PortabilityTests(unittest.TestCase):
    def test_moved_data_uses_copied_images_even_when_original_still_exists(self):
        with tempfile.TemporaryDirectory() as old, tempfile.TemporaryDirectory() as new:
            original = Path(old) / 'batch1' / 'image.jpg'
            copied = Path(new) / 'batch1' / 'image.jpg'
            for file in (original, copied):
                file.parent.mkdir()
                file.write_bytes(b'copied image')
            with patch.object(store, 'DATA', Path(new)):
                store.init()
                with store.db() as con:
                    con.execute("INSERT INTO images(id,batch_id,name,path,status) VALUES(?,?,?,?,?)",
                                ('image1', 'batch1', 'image.jpg', str(original), 'done'))
                store.init()
                with store.db() as con:
                    self.assertEqual(con.execute('SELECT path FROM images').fetchone()[0], str(copied))


class LifecycleTests(unittest.TestCase):
    def test_pause_finishes_current_image_and_continue_preserves_results(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def analyse(model, path):
            calls.append(str(path))
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Test did not release the current image')
            return {'status': 'generated', 'report': {'jewellery_presence': 'present'}, 'attempts': []}
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)), patch.object(service, 'load_engine', FakeModel), patch.object(FakeModel, 'analyse', autospec=True, side_effect=analyse):
            with TestClient(service.app) as client:
                deadline = time.monotonic() + 3
                while client.get('/api/health').json()['status'] != 'ready' and time.monotonic() < deadline:
                    time.sleep(.02)
                bid = client.post('/api/batches', json={'name': 'Pause test'}).json()['id']
                buffer = io.BytesIO(); Image.effect_noise((300, 300), 70).convert('RGB').save(buffer, format='JPEG')
                client.post(f'/api/batches/{bid}/images', files=[('files', (name, buffer.getvalue(), 'image/jpeg')) for name in ('a.jpg', 'b.jpg')])
                client.post(f'/api/batches/{bid}/finish-upload')
                client.post(f'/api/batches/{bid}/run')
                try:
                    self.assertTrue(entered.wait(3))
                    self.assertEqual(client.post(f'/api/batches/{bid}/pause').json()['status'], 'pausing')
                    self.assertEqual(client.post(f'/api/batches/{bid}/continue').status_code, 409)
                    self.assertEqual(client.delete(f'/api/batches/{bid}').status_code, 409)
                    self.assertEqual(client.post(f'/api/batches/{bid}/rerun').status_code, 409)
                finally:
                    release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    paused = client.get(f'/api/batches/{bid}').json()
                    if paused['status'] == 'paused': break
                    time.sleep(.02)
                self.assertEqual(paused['status'], 'paused')
                self.assertEqual(paused['done'], 1)
                self.assertEqual(len(calls), 1)
                self.assertIsNone(paused['started'])
                self.assertIsNone(paused['eta'])
                self.assertEqual(client.get(f'/api/batches/{bid}').json()['elapsed'], paused['elapsed'])
                saved = next(row for row in paused['images'] if row['status'] == 'done')
                self.assertEqual(client.post(f'/api/batches/{bid}/continue').status_code, 200)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    finished = client.get(f'/api/batches/{bid}').json()
                    if finished['status'] == 'finished': break
                    time.sleep(.02)
                self.assertEqual(finished['status'], 'finished')
                self.assertEqual(finished['done'], 2)
                self.assertEqual(len(calls), 2)
                self.assertEqual(next(row for row in finished['images'] if row['id'] == saved['id']), saved)
                self.assertEqual(client.post(f'/api/batches/{bid}/pause').status_code, 409)

    def test_queued_pause_cannot_be_started_by_stale_queue_entry(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)), patch.object(service, 'load_engine', FakeModel):
            with TestClient(service.app) as client:
                deadline = time.monotonic() + 3
                while client.get('/api/health').json()['status'] != 'ready' and time.monotonic() < deadline:
                    time.sleep(.02)
                bid = client.post('/api/batches', json={'name': 'Queued pause'}).json()['id']
                with store.db() as con:
                    con.execute("UPDATE batches SET status='queued' WHERE id=?", (bid,))
                self.assertEqual(client.post(f'/api/batches/{bid}/pause').json()['status'], 'paused')
                service.process_batch(bid)
                self.assertEqual(store.batch(bid)['status'], 'paused')
                store.init()
                self.assertEqual(store.batch(bid)['status'], 'paused')
                with store.db() as con:
                    con.execute("UPDATE batches SET status='pausing',started=? WHERE id=?", (time.time()-1, bid))
                store.init()
                recovered = store.batch(bid)
                self.assertEqual(recovered['status'], 'paused')
                self.assertIsNone(recovered['started'])
                self.assertGreaterEqual(recovered['elapsed'], 1)

    def test_shutdown_stops_worker_and_requests_server_exit(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)), patch.object(service, 'load_engine', FakeModel):
            with TestClient(service.app) as client:
                deadline = time.monotonic() + 3
                while client.get('/api/health').json()['status'] != 'ready' and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(client.post('/api/shutdown').status_code, 503)
                self.assertFalse(service.stop.is_set())
                with patch.object(service.app.state, 'shutdown_callback', create=True) as shutdown:
                    self.assertEqual(client.post('/api/shutdown', headers={'Origin': 'https://example.com'}).status_code, 403)
                    shutdown.assert_not_called()
                    response = client.post('/api/shutdown')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['status'], 'stopping')
                    self.assertTrue(service.stop.is_set())
                    shutdown.assert_called_once_with()

    def test_rerun_uses_fresh_inference_and_delete_hides_batch_and_images(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)), patch.object(service, 'load_engine', FakeModel):
            with TestClient(service.app) as client:
                deadline = time.monotonic() + 3
                while client.get('/api/health').json()['status'] != 'ready' and time.monotonic() < deadline:
                    time.sleep(.02)
                batch_id = client.post('/api/batches', json={'name': 'Fresh set'}).json()['id']
                image = Image.effect_noise((300, 300), 70).convert('RGB')
                buffer = io.BytesIO(); image.save(buffer, format='JPEG')
                upload = client.post(f'/api/batches/{batch_id}/images', files={'files': ('new-image.jpg', buffer.getvalue(), 'image/jpeg')}).json()
                image_id = upload['accepted'][0]
                client.post(f'/api/batches/{batch_id}/finish-upload')
                with patch.object(FakeModel, 'analyse', autospec=True, side_effect=lambda self, path: {'status': 'generated', 'report': {'jewellery_presence': 'present', 'visual_details': {'threads': 'absent'}}, 'attempts': []}) as analyse:
                    for operation in ('run', 'rerun'):
                        self.assertEqual(client.post(f'/api/batches/{batch_id}/{operation}').status_code, 200)
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            result = client.get(f'/api/batches/{batch_id}').json()
                            if result['status'] == 'finished': break
                            time.sleep(.02)
                        self.assertEqual(result['status'], 'finished')
                        self.assertNotIn('threads', result['images'][0]['result']['risk']['categories'])
                    self.assertEqual(analyse.call_count, 2)
                self.assertTrue(list((Path(folder) / 'history' / batch_id).glob('*.json')))
                self.assertEqual(client.delete(f'/api/batches/{batch_id}').status_code, 200)
                self.assertEqual(client.get(f'/api/batches/{batch_id}').status_code, 404)
                self.assertEqual(client.get(f'/api/images/{image_id}').status_code, 404)
                self.assertEqual(client.get('/api/batches').json(), [])

    def test_upload_run_persist_and_export(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)), patch.object(service, 'load_engine', FakeModel):
            with TestClient(service.app) as client:
                deadline=time.monotonic()+3
                while client.get('/api/health').json()['status']!='ready' and time.monotonic()<deadline: time.sleep(.02)
                batch=client.post('/api/batches',json={'name':'Nested folder'}).json()
                batch_id=batch['id']
                self.assertEqual(client.post(f'/api/batches/{batch_id}/finish-upload').status_code,400)
                image=Image.effect_noise((300,300),70).convert('RGB')
                buffer=io.BytesIO();image.save(buffer,format='JPEG')
                response=client.post(f'/api/batches/{batch_id}/images',files=[('files',('../../nested/image.jpg',buffer.getvalue(),'image/jpeg')),('files',('corrupt.jpg',b'not an image','image/jpeg')),('files',('ignore.txt',b'nothing','text/plain'))])
                self.assertEqual(len(response.json()['accepted']),2)
                self.assertEqual(len(response.json()['rejected']),1)
                self.assertFalse((Path(folder).parent/'nested/image.jpg').exists())
                self.assertEqual(client.post(f'/api/batches/{batch_id}/finish-upload').status_code,200)
                self.assertEqual(client.post(f'/api/batches/{batch_id}/run').status_code,200)
                deadline=time.monotonic()+4
                while time.monotonic()<deadline:
                    result=client.get(f'/api/batches/{batch_id}').json()
                    if result['status']=='finished':break
                    time.sleep(.02)
                self.assertEqual(result['status'],'finished')
                self.assertEqual(result['done'],2)
                self.assertEqual(result['errors'],1)
                self.assertEqual(result['images'][0]['status'],'error')
                good=next(i for i in result['images'] if i['status']=='done')
                self.assertIn('threads',good['result']['risk']['categories'])
                self.assertEqual(client.get('/api/images/'+good['id']+'?thumbnail=true').status_code,200)
                exported=client.get(f'/api/batches/{batch_id}/export')
                self.assertEqual(len(exported.json()['images']),2)
                self.assertIn('attachment',exported.headers['content-disposition'])
                self.assertEqual(client.post(f'/api/batches/{batch_id}/run').status_code,409)
                self.assertEqual(client.post('/api/batches',json={},headers={'Origin':'https://example.com'}).status_code,403)
            # Restart recovery changes only interrupted work, preserving finished results.
            with store.db() as con:
                con.execute("INSERT INTO batches(id,name,status,created,started) VALUES('interrupted','Test','running',?,?)", (time.time(),time.time()-2))
            store.init()
            self.assertEqual(store.batch(batch_id)['status'],'finished')
            self.assertEqual(store.batch('interrupted')['status'],'interrupted')
            self.assertGreaterEqual(store.batch('interrupted')['elapsed'],2)


if __name__=='__main__':unittest.main()
