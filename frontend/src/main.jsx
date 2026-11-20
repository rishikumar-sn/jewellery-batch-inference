import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import companyLogo from './assets/embsys_logo.svg';

const filters = [['priority_review','Priority review'],['all','All images'],['audit_sample','Low priority audit sample'],['critical','Critical'],['high','High'],['medium','Medium'],['low','Low'],['unassessed','Unassessed'],['jewellery_absent','No jewellery'],['threads','Threads'],['weight_present','Weight value present'],['packet_present','Packet'],['scale_present','Weight scale'],['weight_unreadable','Unreadable weight'],['black_beads','Black beads'],['possible_mangalsutra','Possible mangalsutra'],['coloured_stones_or_beads','Coloured stones / beads'],['bundled','Bundled items'],['obscured','Obscured items'],['poor_quality','Image quality'],['contradictions','Prediction conflicts'],['needs_review','Technical / presence review']];
const formats = /\.(jpe?g|png|webp|bmp|tiff?)$/i;
const duration = s => s == null ? 'Estimating…' : s < 60 ? `${Math.floor(s)}s` : s < 3600 ? `${Math.floor(s/60)}m ${Math.floor(s%60)}s` : `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;
const label = s => (s || '').replaceAll('_',' ');
const primaryFilters = new Set(['priority_review', 'all', 'jewellery_absent', 'weight_present',
  'weight_unreadable', 'threads', 'black_beads', 'coloured_stones_or_beads']);
const riskFilters = ['critical', 'high', 'medium', 'low'];
const jewelNames = {necklace_chain: 'Necklace or chain', earring_nosepin: 'Earring or nose ornament',
  not_known: 'Uncertain type'};
const detailNames = {threads: 'Thread or cord', black_beads: 'Black beads',
  coloured_stones_or_beads: 'Coloured stones or beads'};
function hasFinding(image, finding) {
  const categories = image.result?.risk?.categories || [];
  if (categories.includes(finding)) return true;
  if (finding === 'packet_present') return categories.includes('packet_inside') || image.result?.report?.inspection_subject === 'packet_contents';
  return finding === 'possible_mangalsutra' && categories.includes('black_beads') && image.result?.report?.jewellery_presence === 'present';
}
const inFilter = (image, filter) => (filter === 'priority_review' && (['critical','high','unassessed'].includes(image.result?.risk?.tier) || ['failed','needs_verification'].includes(image.result?.processing_status))) || filter === 'all' || image.result?.risk?.tier === filter || hasFinding(image, filter);
async function api(path, options) {
  const response = await fetch('/api' + path, options);
  if (!response.ok) {let error; try {error = await response.json()} catch {} throw new Error(error?.detail || `Request failed (${response.status})`)}
  return response.json();
}

function ImageFindings({result}) {
  if (!result) return <p>Waiting for inspection.</p>;
  const {report, risk} = result;
  const types = (report?.possible_jewel_types || []).map(({type, name}) => name || jewelNames[type] || label(type));
  const details = Object.entries(detailNames).filter(([key]) => report?.visual_details?.[key] === 'present')
    .map(([, name]) => name);
  const scale = report?.scale;
  const weight = scale?.weight_text && scale.display !== 'unreadable' ? scale.weight_text : null;
  const displayStatus = {unreadable: 'Unreadable', not_visible: 'Display out of view', unknown: 'Reading unconfirmed'};
  const presence = {present: 'Visible', absent: 'None visible', unknown: 'Uncertain'};
  const row = (title, value) => <div><dt>{title}</dt><dd>{value}</dd></div>;
  return <>
    <section className="image-description">
      <h3>Description</h3>
      <p>{report?.description || (result.status === 'skipped_quality' ? 'Image quality prevents inspection.' : 'Description unavailable.')}</p>
    </section>
    <section className="image-results">
      <div className="image-results-heading">
        <h3>Results</h3>
        {risk && <span className={'risk-badge ' + risk.tier}>{risk.tier === 'unassessed' ? 'Needs review' : label(risk.tier) + ' priority'}</span>}
      </div>
      <dl className="result-rows">
        {row('Jewellery', presence[report?.jewellery_presence] || 'Unassessed')}
        {weight ? row('Scale reading', <strong>{weight}</strong>) :
          scale?.presence === 'present' && row('Scale reading', displayStatus[scale.display] || 'Unavailable')}
        {report?.jewellery_presence === 'present' && row('Type', types.join(', ') || 'Uncertain')}
        {report?.jewellery_presence === 'present' && row('Count', report.jewel_count ?? 'Uncertain')}
        {!!details.length && row('Visible features', details.join(', '))}
      </dl>
    </section>
    {(risk?.reasons?.length > 0 || report?.assessment || result.observation_notes?.length > 0 || result.error) &&
      <details className="image-assessment">
        <summary>Assessment and review notes</summary>
        {!!risk?.reasons?.length && <ul className="reasons">{risk.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
        {report?.assessment && <dl className="result-rows">{Object.entries(report.assessment).map(([key, value]) =>
          <div key={key}><dt>{label(key)}</dt><dd>{label(value)}</dd></div>)}</dl>}
        {!!result.observation_notes?.length && <ul className="reasons">{result.observation_notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
        {result.error && <p className="detail-error">{result.error}</p>}
      </details>}
  </>;
}
function App() {
  const [health,setHealth] = useState({status:'loading'}), [batches,setBatches] = useState([]), [active,setActive] = useState(null), [batch,setBatch] = useState(null), [filter,setFilter] = useState('priority_review'), [search,setSearch] = useState(''), [upload,setUpload] = useState(null), [error,setError] = useState(''), [selected,setSelected] = useState(null), [busy,setBusy] = useState(false), [limit,setLimit] = useState(60);
  const [shutdownState,setShutdownState] = useState('');
  const shutdownRef = useRef(false);
  const picker = useRef(), dialog = useRef(), activeRef = useRef(null);
  activeRef.current = active;
  async function refresh() {
    const [h,b] = await Promise.all([api('/health'),api('/batches')]);
    setHealth(h);setBatches(b);
    const id = activeRef.current;
    if (id) {const d = await api('/batches/'+id);if (activeRef.current === id) setBatch(d)}
  }
  useEffect(() => {let mounted=true; let timer; async function poll(){try {if(mounted && !shutdownRef.current) await refresh()}catch(e){if(mounted && !shutdownRef.current)setError(e.message)}finally{if(mounted && !shutdownRef.current)timer=setTimeout(poll,1500)}} poll();return()=>{mounted=false;clearTimeout(timer)}},[shutdownState]);
  useEffect(()=>{setBatch(null);setFilter('priority_review');setSearch('');setLimit(60);if(active)api('/batches/'+active).then(d=>{if(activeRef.current===active)setBatch(d)}).catch(e=>setError(e.message))},[active]);
  useEffect(()=>{setLimit(60)},[filter,search]);
  useEffect(()=>{if(selected){dialog.current.showModal();dialog.current.focus()}},[selected]);
  async function uploadFolder(files) {
    if(upload || !files.length)return;
    setError('');setUpload({done:0,total:files.length});
    let created;
    try {
      const supported = [...files].filter(f=>formats.test(f.name));
      if(!supported.length)throw new Error('No supported images in this folder. Choose JPG, PNG, WebP, BMP or TIFF images.');
      const name = (supported[0].webkitRelativePath || '').split('/')[0] || 'Image batch';
      created = await api('/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
      const rejected=[];
      // Bounded requests keep uploads responsive for large folders.
      for(let i=0;i<supported.length;i+=8) {
        const form=new FormData();supported.slice(i,i+8).forEach(file=>form.append('files',file,file.webkitRelativePath || file.name));
        const result=await api(`/batches/${created.id}/images`,{method:'POST',body:form});rejected.push(...result.rejected);
        setUpload({done:Math.min(i+8,supported.length),total:supported.length});
      }
      await api(`/batches/${created.id}/finish-upload`,{method:'POST'});
      setActive(created.id);
      if(rejected.length)setError(`${rejected.length} image(s) could not be uploaded: ${rejected.map(r=>r.name+' — '+r.reason).join('; ')}`);
      await refresh();
    }catch(e){setError(e.message);if(created)setActive(created.id)}finally{setUpload(null);if(picker.current)picker.current.value=''}
  }
  async function shutdown() {
    if(!window.confirm('Shut down the inspection application? Active inspections will pause and can be resumed after restarting.'))return;
    shutdownRef.current=true;setShutdownState('stopping');setError('');
    try {await api('/shutdown',{method:'POST'});setShutdownState('stopped')}
    catch(e){shutdownRef.current=false;setShutdownState('');setError(e.message)}
  }
  async function run() {setBusy(true);setError('');try{await api(`/batches/${active}/run`,{method:'POST'});await refresh()}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function controlBatch(operation) {setBusy(true);setError('');try{await api(`/batches/${active}/${operation}`,{method:'POST'});await refresh()}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function rerun() {if(!window.confirm('Run this batch again? Previous results will be archived.'))return;setBusy(true);setError('');try{await api(`/batches/${active}/rerun`,{method:'POST'});await refresh()}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function removeBatch() {if(!window.confirm('Delete this batch from the workspace?'))return;setBusy(true);setError('');try{await api(`/batches/${active}`,{method:'DELETE'});setActive(null);setBatch(null);await refresh()}catch(e){setError(e.message)}finally{setBusy(false)}}
  const images=batch?.images || [], visible=images.filter(image=>inFilter(image,filter) && image.name.toLowerCase().includes(search.toLowerCase()));
  const inspected=images.find(i=>i.id===selected?.id) || selected;
  const renderFilter = ([key, title]) => <button key={key} aria-pressed={filter===key}
    className={filter===key?'selected':''} onClick={()=>setFilter(key)}>
    {title}<span>{images.filter(image=>inFilter(image,key)).length}</span>
  </button>;
  const processing=['running','queued','pausing'].includes(batch?.status), progress=batch?.total ? batch.done/batch.total*100 : 0;
  const finished=batches.filter(b=>b.status==='finished').length;
  return <div className="app">
    <header><a className="brand" href="/" aria-label="Embsys home"><img className="company-logo" src={companyLogo} alt="Embsys Intelligence" width="865" height="288"/></a><div className="header-actions"><div className="system"><span className={'dot '+health.status}/>{health.status==='ready' ? 'AI ready' : health.status==='error' ? 'AI unavailable' : 'Preparing AI...'}</div><button className="shutdown-button" disabled={!!shutdownState || !!upload || busy} onClick={shutdown}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M12 3v9M7 5.5a8 8 0 1 0 10 0"/></svg>Shut down</button></div></header>
    <main><div className="page-title"><div><span className="eyebrow">INSPECTION WORKSPACE</span><h1>Jewellery image inspection</h1></div><button className="primary" disabled={!!upload} onClick={()=>picker.current.click()}><span aria-hidden="true">+</span> {upload ? 'Uploading...' : 'Upload image folder'}</button><input hidden ref={picker} type="file" webkitdirectory="" directory="" multiple onChange={e=>uploadFolder(e.target.files)} /></div>
    {error && <div role="alert" className="error">{error}<button aria-label="Dismiss message" onClick={()=>setError('')}>×</button></div>}
    {health.status==='error' && <div className="error" role="alert">{health.error}</div>}
    {upload && <div className="upload-status" role="status"><div><strong>Uploading folder</strong><span>{upload.done} / {upload.total} images</span></div><progress value={upload.done} max={upload.total}/></div>}
    <div className="workspace"><aside><div className="aside-title"><h2>Batches</h2><span>{batches.length}</span></div><div className="batch-list">{batches.map(b=><button key={b.id} className={'batch '+(active===b.id?'active':'')} onClick={()=>setActive(b.id)}><div className="folder-icon" aria-hidden="true">▱</div><div className="batch-text"><strong>{b.name}</strong><small>{b.total} images · {new Date(b.created*1000).toLocaleDateString('en-IN',{day:'numeric',month:'short'})}</small><span className={'status '+b.status}>{label(b.status)}</span></div>{b.status==='finished' && <span className="check">✓</span>}</button>)}</div><div className="aside-bottom"><strong>{finished}</strong> completed {finished===1?'batch':'batches'}<small>Stored on this computer</small></div></aside>
    <section className="content" aria-label="Batch inspection">
    {!active ? <div className="empty"><div className="empty-art" aria-hidden="true">▧</div><span className="eyebrow">BATCH INSPECTION</span><h2>Create your first inspection batch</h2><p>Upload a folder to create a batch, then run the inspection.<br/>Your results will be organised by risk and visible findings.</p><button className="primary" disabled={!!upload} onClick={()=>picker.current.click()}>Choose a folder</button><small>JPG, PNG, WebP, BMP and TIFF · Subfolders included</small></div> : !batch ? <div className="empty" role="status"><span className="spinner"/>Loading batch…</div> : <>
    <div className="batch-heading"><div><span className="eyebrow">BATCH OVERVIEW</span><h2>{batch.name}</h2><p>{batch.total} images <span>·</span> <span className={'status '+batch.status}>{label(batch.status)}</span></p></div><div className="heading-actions">{['running','queued'].includes(batch.status) && <button className="secondary" disabled={busy} onClick={()=>controlBatch('pause')}>Pause</button>}{batch.status==='pausing' && <button className="secondary" disabled>Pausing...</button>}{batch.status==='paused' && <button className="primary" disabled={busy || health.status!=='ready'} onClick={()=>controlBatch('continue')}>Resume</button>}{['ready','running','queued','pausing','paused','interrupted','finished'].includes(batch.status) && <button className="secondary" disabled={busy || health.status!=='ready' || processing} title={processing?'Pause the batch before rerunning it':'Restart every image; previous results will be archived'} onClick={rerun}>Rerun batch</button>}{!['running','queued','pausing'].includes(batch.status) && <button className="text-action" disabled={busy} onClick={removeBatch}>Delete batch</button>}{batch.status==='finished' && <a className="secondary" href={`/api/batches/${active}/export`}>Export results</a>}{['ready','interrupted'].includes(batch.status) && <button className="primary" disabled={busy || health.status!=='ready'} onClick={run}>{busy?'Starting…':batch.status==='interrupted'?'Resume inspection':'Run inspection'}</button>}{batch.status==='uploading' && batch.total>0 && <button className="primary" onClick={async()=>{try{await api(`/batches/${active}/finish-upload`,{method:'POST'});await refresh()}catch(e){setError(e.message)}}}>Finish upload</button>}</div></div>
    {(processing || ['finished','interrupted','paused'].includes(batch.status)) && <div className="run-progress"><div className="progress-title"><strong>{batch.status==='finished'?'Inspection finished':batch.status==='queued'?'Waiting for current batch':batch.status==='interrupted'?'Inspection paused after restart':batch.status==='pausing'?'Finishing current image before pausing':batch.status==='paused'?'Inspection paused':'Inspecting your images'}</strong><span>{batch.done} / {batch.total}</span></div><progress value={batch.done} max={batch.total || 1}/>{batch.status==='paused' && <p className="pause-note" role="status">Completed results are saved. Resume to process the remaining images.</p>}{batch.status==='pausing' && <p className="pause-note" role="status">The current image will finish, then processing will pause.</p>}<div className="timing"><span>Elapsed <strong>{duration(batch.elapsed)}</strong></span>{batch.status==='running' && <span>Estimated remaining <strong>{duration(batch.eta)}</strong></span>}{batch.errors>0 && <span>{batch.errors} need manual review</span>}<span>{Math.floor(progress)}% complete</span></div></div>}
    <div className="risk-summary">{riskFilters.map(tier=><button key={tier} aria-pressed={filter===tier} className={'risk-stat '+tier+(filter===tier?' chosen':'')} onClick={()=>setFilter(filter===tier?'all':tier)}><span><i/>{label(tier)}</span><strong>{images.filter(i=>i.result?.risk?.tier===tier).length}</strong></button>)}</div>
    <div className="findings">
      <div className="filters">{filters.filter(([key]) => primaryFilters.has(key)).map(renderFilter)}</div>
      <details key={active} className="more-filters">
        <summary>More filters</summary>
        <div className="filters">{filters.filter(([key]) => !primaryFilters.has(key) && !riskFilters.includes(key)).map(renderFilter)}</div>
      </details>
    </div>
    <div className="results-heading"><div><h3>{filters.find(f=>f[0]===filter)?.[1]}</h3><small>{visible.length} images · Highest risk first</small></div><input className="search" aria-label="Search images by filename" placeholder="Search filename…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
    {!visible.length ? <div className="no-results">{images.length ? filter==='priority_review'?'No priority review cases.':'No images in this view.' : 'No images loaded yet.'}</div> : <div className="image-grid">{visible.slice(0,limit).map(image=><button className="image-card" key={image.id} onClick={()=>setSelected(image)}><div className="image-preview"><img loading="lazy" src={`/api/images/${image.id}?thumbnail=true`} alt={image.name} onError={e=>{e.currentTarget.style.visibility='hidden'}}/>{image.result?.risk && <span className={'risk-badge '+image.result.risk.tier}>{label(image.result.risk.tier)}</span>}</div><div className="image-info"><strong title={image.name}>{image.name.split('/').pop()}</strong>{image.status !== 'done' && <small>{image.status === 'error' ? 'Needs review' : 'Pending inspection'}</small>}</div></button>)}</div>}
    {visible.length>limit && <button className="secondary more" onClick={()=>setLimit(limit+60)}>Show more images ({visible.length-limit} remaining)</button>}
    </>}
    </section></div><footer><span>Embsys · Jewellery image inspection</span><span>Local workspace</span></footer></main>
    {shutdownState && <div className="shutdown-overlay" role="status" aria-live="polite"><div className="shutdown-message"><span className="eyebrow">APPLICATION</span><h2>{shutdownState==='stopped'?'Application shut down':'Shutting down...'}</h2><p>{shutdownState==='stopped'?'Your saved batches are available when you restart the application. You can close this tab.':'Stopping the inspection service and saving progress.'}</p></div></div>}
    {selected && <dialog ref={dialog} className="detail-dialog" aria-labelledby="image-details-name"
      onCancel={()=>setSelected(null)} onClick={e=>{if(e.target===e.currentTarget){dialog.current.close();setSelected(null)}}}>
      <button className="close-dialog" autoFocus aria-label="Close image details" onClick={()=>{dialog.current.close();setSelected(null)}}>&times;</button>
      <div className="detail-layout">
        <div className="detail-image">
          <img src={`/api/images/${inspected.id}`} alt={inspected.name}/>
          <a href={`/api/images/${inspected.id}`} target="_blank" rel="noreferrer">Open full image</a>
        </div>
        <div className="detail-copy">
          <h2 id="image-details-name" title={inspected.name}>{inspected.name.split('/').pop()}</h2>
          <ImageFindings result={inspected.result}/>
        </div>
      </div>
    </dialog>}
  </div>
}
createRoot(document.getElementById('root')).render(<App/>);
