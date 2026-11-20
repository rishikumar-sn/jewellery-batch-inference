"""Optional real-model smoke test against a running local server."""
import json
from pathlib import Path
import sys
import time
import httpx

root=Path(__file__).resolve().parents[1]
source=Path(sys.argv[1]) if len(sys.argv)>1 else root.parent/'AU-Image-Analysis/AU-images-v1'
output=root/'test-output'
output.mkdir(exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:5050',timeout=120,trust_env=False) as client:
    assert client.get('/api/health').json()['status']=='ready'
    assert client.get('/').status_code==200
    batch=client.post('/api/batches',json={'name':'Verification batch'}).json()
    batch_id=batch['id']
    sources=list(source.glob('*.jpg'))[:2]
    assert len(sources)==2
    for path in sources:
        with path.open('rb') as stream:
            response=client.post(f'/api/batches/{batch_id}/images',files={'files':(path.name,stream,'image/jpeg')})
        assert len(response.json()['accepted'])==1,response.text
    client.post(f'/api/batches/{batch_id}/images',files={'files':('unreadable.jpg',b'corrupt sample','image/jpeg')}).raise_for_status()
    client.post(f'/api/batches/{batch_id}/finish-upload').raise_for_status()
    client.post(f'/api/batches/{batch_id}/run').raise_for_status()
    (output/'live-batch-id.txt').write_text(batch_id)
    deadline=time.monotonic()+600
    previous=None
    while time.monotonic()<deadline:
        result=client.get(f'/api/batches/{batch_id}').json()
        progress=(result['status'],result['done'])
        if progress!=previous:
            print(progress,flush=True)
            previous=progress
        if result['status']=='finished':break
        time.sleep(2)
    assert result['status']=='finished',result
    assert result['total']==3 and result['done']==3 and result['errors']==1
    generated=[image for image in result['images'] if image['status']=='done']
    assert len(generated)==2
    assert all(image['result'].get('report') for image in generated)
    assert client.get('/api/images/'+generated[0]['id']+'?thumbnail=true').status_code==200
    exported=client.get(f'/api/batches/{batch_id}/export')
    assert len(exported.json()['images'])==3
    (output/'live-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    for image in result['images']:
        print(image['name'],image['status'],image['result']['risk']['tier'],round(image['seconds'],2),flush=True)
