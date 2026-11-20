"""Restore the exact original Vultr revision; never substitute another quantization."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
REVISION='eefa5164a6ef76f9a5577945555392c79a77686c'
NAME='Qwen3.5-2B-Q4_K_M.gguf'
SIZE=1384014496
EXPECTED='cd1950c433492e0bbf2e64f32486c525af0e0bf2ab98dd73c329e597ef282f2d'

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def restore():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    target=Path(config['model'])
    if target.exists() and digest(target)==EXPECTED:
        print('Original Vultr model already matches the published checksum.',flush=True)
        return
    temporary=target.with_suffix('.gguf.verified-download')
    with temporary.open('wb') as stream:stream.truncate(SIZE)
    chunk_size=8*1024*1024
    parts=[(start,min(SIZE-1,start+chunk_size-1)) for start in range(0,SIZE,chunk_size)]
    def transfer(part):
        start,end=part
        for attempt in range(4):
            try:
                url=f'https://huggingface.co/vultr/Qwen3.5-2B-GGUF/resolve/{REVISION}/{NAME}?download=true&repair={start}'
                request=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(request,timeout=45) as response:
                    if response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {start}-{end}/'):
                        raise RuntimeError('Invalid model download range')
                    data=response.read()
                if len(data)!=end-start+1:raise RuntimeError('Incomplete model range')
                with temporary.open('r+b') as stream:stream.seek(start);stream.write(data)
                return len(data)
            except Exception:
                if attempt==3:raise
                time.sleep(attempt+1)
    done=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for future in concurrent.futures.as_completed([pool.submit(transfer,part) for part in parts]):
            done+=future.result()
            if done//(128*1024*1024)!=(done-chunk_size)//(128*1024*1024) or done==SIZE:print(f'Original Vultr model: {done/SIZE:.0%}',flush=True)
    actual=digest(temporary)
    if actual!=EXPECTED:raise RuntimeError('Restored model checksum mismatch: '+actual)
    if target.exists():
        backup=target.with_suffix('.gguf.before-verified-restore-'+str(time.time_ns()))
        target.rename(backup)
        print('Preserved previous model:',backup,flush=True)
    temporary.replace(target)
    manifest_path=ROOT/'reference/testing-artifacts.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['model']['sha256']=EXPECTED
    manifest['model']['repo']='vultr/Qwen3.5-2B-GGUF'
    manifest['model']['revision']=REVISION
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Restored and verified the exact original Vultr model. Projector unchanged.',flush=True)

if __name__=='__main__':restore()
