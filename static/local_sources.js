'use strict';
// Sources are shared; every processing workflow owns its preferences.
let localSources=[],browserFiles=new Map(),sourceSockets=new Map(),pickerActive=null,lastBatch='',batchRequest=null,hasSavedExportSettings=false;
let exportSettings={outputs:{frames:false,audio:false,puzzles:true,transcript:true,clips:false,ai:true,zip:false},dedup:{enabled:false,mode:'exact',threshold:4},segments:{enabled:false,seconds:10,organize:true}};
let serverSettingsLoaded=false,serverSaveTimer=null,workflowSettings={},activeWorkflow='board',dialogSettings=null,savedAt25=0;
function workflowDefaults(kind){return {outputs:{frames:kind==='rules',audio:false,puzzles:kind!=='rules',transcript:kind==='board',clips:false,ai:kind==='board',zip:false},dedup:{enabled:false,mode:'exact',threshold:4},segments:{enabled:false,seconds:10,organize:true},ai_package:{enabled:kind==='board',combined:false,screen_text:true,ocr_threads:2,scan_seconds:1,batch_files:10},performance:'auto',fields:{},online_episodes:{mode:'separate',items:[]}};}
function migrateWorkflows(saved={}){for(const kind of ['board','rules','subtitles']){const d=workflowDefaults(kind),old=saved.workflows?.[kind]||(kind==='board'?saved.settings:null)||{};for(const key of Object.keys(d)){if(old[key]!==undefined)d[key]=typeof d[key]==='object'?{...d[key],...old[key]}:old[key];}if(kind==='rules'){Object.assign(d.outputs,{puzzles:false,transcript:false,ai:false});Object.assign(d.ai_package,{enabled:false,combined:false});}if(kind==='board'){Object.assign(d.outputs,{puzzles:true,transcript:true,ai:true});d.ai_package.enabled=true;}workflowSettings[kind]=d;}exportSettings=workflowSettings[activeWorkflow];}
try{const saved=JSON.parse(localStorage.getItem('yingge21')||'null');if(saved){localSources=saved.sources||[];exportSettings=saved.settings||exportSettings;hasSavedExportSettings=Boolean(saved.settings);lastBatch=saved.batch||'';batchRequest=saved.pending||null;}}catch{}
migrateWorkflows({settings:exportSettings});
try{const saved=JSON.parse(localStorage.getItem('yingge25')||'null');if(saved?.workflows){migrateWorkflows(saved);savedAt25=saved.saved_at||0;hasSavedExportSettings=true;}}catch{}
const sourceLabels={ready:'可处理',validating:'检查视频中',invalid:'不可读',waiting_source:'等待重新选择'};
function saveLocalState(){if(serverSettingsLoaded)savedAt25=Date.now();const saved={saved_at:savedAt25,sources:localSources,settings:workflowSettings.board,workflows:workflowSettings,batch:lastBatch,pending:batchRequest};try{localStorage.setItem('yingge25',JSON.stringify(saved));localStorage.setItem('yingge21',JSON.stringify(saved));}catch{}if(serverSettingsLoaded){clearTimeout(serverSaveTimer);serverSaveTimer=setTimeout(()=>api('/api/batch-settings',{method:'POST',body:{saved_at:savedAt25,source_ids:localSources.map(s=>s.id),settings:workflowSettings.board,workflows:workflowSettings,batch:lastBatch,pending:batchRequest}}).catch(e=>toast('设置未保存到本机服务：'+e.message,true)),300);}}
function settingsFor(panel){return panel?.closest('#commonExportDialog')?dialogSettings||exportSettings:workflowSettings[panel?.dataset.workflow||activeWorkflow];}
function activateWorkflow(kind){activeWorkflow=kind;exportSettings=workflowSettings[kind];syncExportPanels();}
function mountSourcePanel(host,prefix){
  host.innerHTML=`<div class="source-drop" tabindex="0" role="region" aria-label="拖入多个视频"><strong>拖入一个或多个视频</strong><small>按需读取原文件；处理拖入文件时请保留此网页。</small><div class="button-row"><button data-source-action="files" class="subtle">选择文件（可多选）</button><button data-source-action="folder" class="subtle">选择文件夹</button><button data-source-action="paste" class="text-btn">粘贴路径</button><button data-source-action="web-files" class="text-btn">网页选取文件</button><button data-source-action="cancel-picker" class="text-btn" hidden>取消选择</button></div></div><label class="checkbox-line"><input data-source-recursive type="checkbox" checked> 包含子文件夹（跳过目录联接）</label><div data-path-panel hidden><textarea data-source-paths rows="4" placeholder='每行一个完整路径，可带双引号；文件名中的空格、逗号保留'></textarea><button data-source-action="paths" class="subtle">追加这些路径</button></div><div class="source-list" data-source-list></div><div class="button-row"><button data-source-action="clear" class="text-btn">清空列表</button><span data-source-count class="mini-note"></span></div><p data-source-note class="mini-note" role="status"></p><video data-source-preview controls hidden></video><img data-source-cover hidden alt="视频封面"><input data-source-web type="file" multiple accept="video/*,.mkv,.flv,.ts,.m2ts" hidden><input data-source-reselect type="file" accept="video/*,.mkv,.flv,.ts,.m2ts" hidden>`;
  const drop=host.querySelector('.source-drop');
  for(const event of ['dragover','dragenter'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('dragging');});
  drop.addEventListener('dragleave',()=>drop.classList.remove('dragging'));
  drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragging');const entries=[...e.dataTransfer.items].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);const files=[...e.dataTransfer.files];(entries.length?readDroppedEntries(entries,host.querySelector('[data-source-recursive]').checked).then(handleBrowserFiles):handleBrowserFiles(files)).catch(e=>toast(e.message,true));});
  const webFiles=host.querySelector('[data-source-web]');webFiles.onchange=()=>{const files=[...webFiles.files];webFiles.value='';handleBrowserFiles(files).catch(e=>toast(e.message,true));};
  const webFolder=document.createElement('input');webFolder.type='file';webFolder.multiple=true;webFolder.setAttribute('webkitdirectory','');webFolder.hidden=true;webFolder.dataset.sourceFolder='';host.append(webFolder);
  webFolder.onchange=()=>{let files=[...webFolder.files];webFolder.value='';if(!host.querySelector('[data-source-recursive]').checked)files=files.filter(f=>(f.webkitRelativePath||f.name).split('/').length<=2);handleBrowserFiles(files,true).catch(e=>toast(e.message,true));};
  const folderButton=host.querySelector('[data-source-action="folder"]');folderButton.textContent='导入文件夹';
  const webFolderButton=document.createElement('button');webFolderButton.className='text-btn';webFolderButton.dataset.sourceAction='web-folder';webFolderButton.textContent='网页选择文件夹';folderButton.after(webFolderButton);
  drop.querySelector('strong').textContent='选择本机视频，或拖入视频 / 文件夹';
  const fallback=document.createElement('details');fallback.className='source-fallback';fallback.innerHTML='<summary>备用网页选择方式</summary><p class="mini-note">按需读取、不复制原片；处理期间保留此网页。</p>';drop.append(fallback);fallback.append(host.querySelector('[data-source-action="web-files"]'),webFolderButton);
  const reselect=host.querySelector('[data-source-reselect]');
  reselect.onchange=async()=>{const f=reselect.files[0],id=reselect.dataset.id;reselect.value='';if(f)try{await addBrowserFile(f,id);}catch(e){toast(e.message,true);}};
  host.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button||button.disabled)return;
    try{const action=button.dataset.sourceAction,id=button.dataset.sourceId;
      if(action==='web-files')webFiles.click();
      else if(action==='web-folder')webFolder.click();
      else if(action==='paste'){host.querySelector('[data-path-panel]').hidden=false;host.querySelector('textarea').focus();}
      else if(action==='paths')await busy(button,async()=>{const raw=host.querySelector('textarea').value;const result=await api('/api/sources/register',{method:'POST',body:{paths:raw}});appendSources(result.sources);});
      else if(action==='files'||action==='folder')await nativePick(action,host);
      else if(action==='cancel-picker'&&pickerActive){await api(`/api/pickers/${pickerActive}/cancel`,{method:'POST'});}
      else if(action==='remove'){detachSource(id);localSources=localSources.filter(s=>s.id!==id);renderLocalSources();}
      else if(action==='clear'){for(const s of localSources)detachSource(s.id);localSources=[];renderLocalSources();}
      else if(action==='preview')await sourcePreview(id,host);
      else if(action==='reselect'){reselect.dataset.id=id;reselect.click();}
      else if(action==='validate'){await api(`/api/sources/${id}/validate`,{method:'POST'});const s=localSources.find(s=>s.id===id);if(s){s.status='validating';s.error='';renderLocalSources();}}
    }catch(e){toast(e.message,true);}
  });
}
function appendSources(rows){for(const source of rows){const at=localSources.findIndex(s=>s.id===source.id);if(at<0)localSources.push(source);else localSources[at]=source;}renderLocalSources();}
function detachSource(id){browserFiles.delete(id);const socket=sourceSockets.get(id);sourceSockets.delete(id);socket?.close();}
function renderLocalSources(){
  for(const host of document.querySelectorAll('.local-source-panel')){
    host.querySelector('[data-source-list]').innerHTML=localSources.map(s=>`<article class="source-row ${s.status==='invalid'?'invalid':''}"><div><strong>${html(s.name)}</strong><small title="${html(s.path||s.name)}">${html(s.path||'网页文件 · 不保存原片')}</small><small>${html(sourceLabels[s.status]||s.status)}${s.size?` · ${(s.size/1048576).toFixed(1)} MB`:''}${s.error?' · '+html(s.error):''}</small></div><div class="button-row"><button class="text-btn" data-source-action="preview" data-source-id="${s.id}">预览</button>${s.kind==='browser'?`<button class="text-btn" data-source-action="reselect" data-source-id="${s.id}">重新选择</button>`:`<button class="text-btn" data-source-action="validate" data-source-id="${s.id}">重新检查</button>`}<button class="text-btn" data-source-action="remove" data-source-id="${s.id}">移除</button></div></article>`).join('');
    host.querySelector('[data-source-count]').textContent=`${localSources.length} 个视频 · ${localSources.filter(s=>s.status==='invalid').length} 个不可读`;
  }
  saveLocalState();
}
async function nativePick(kind,host){
  if(pickerActive)throw new Error('已有选择窗口打开，可点击“取消选择”后重新打开');
  const controls=[...document.querySelectorAll('[data-source-action="files"],[data-source-action="folder"]')];
  controls.forEach(b=>b.disabled=true);
  document.querySelectorAll('[data-source-action="cancel-picker"]').forEach(b=>b.hidden=false);
  try{
    const task=await api('/api/pickers',{method:'POST',body:{kind}});pickerActive=task.id;
    let result;
    do{await new Promise(r=>setTimeout(r,500));result=await api(`/api/pickers/${task.id}`);}while(result.status==='pending');
    if(result.status==='error')throw new Error(result.error||'选择失败，请重新选择');
    if(result.status!=='done'||!result.paths.length)return;
    if(kind==='files')appendSources((await api('/api/sources/register',{method:'POST',body:{paths:result.paths}})).sources);
    else{
      const result2=await api('/api/sources/discover',{method:'POST',body:{folder:result.paths[0],recursive:host.querySelector('[data-source-recursive]').checked}});
      appendSources(result2.sources);host.querySelector('[data-source-note]').textContent=result2.skipped.map(s=>s.reason+'：'+s.name).join('\n')||`发现 ${result2.sources.length} 个视频`;
    }
  }finally{pickerActive=null;controls.forEach(b=>b.disabled=false);document.querySelectorAll('[data-source-action="cancel-picker"]').forEach(b=>b.hidden=true);}
}
async function fingerprint(file){
  const n=1048576,parts=[file.slice(0,Math.min(n,file.size)),file.slice(Math.max(n,file.size-n),file.size)];
  const data=await new Blob(parts).arrayBuffer();return [...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(x=>x.toString(16).padStart(2,'0')).join('');
}
const localVideoExtension=/\.(mp4|mkv|mov|webm|avi|flv|ts|m4v|m2ts|mpeg|mpg|wmv|3gp|mts)$/i;
async function readDroppedEntries(entries,recursive){
  const files=[];
  async function visit(entry,depth){
    if(files.length>=10000)throw new Error('文件夹视频超过 10000 个，请分批导入');
    if(entry.isFile){if(localVideoExtension.test(entry.name))files.push(await new Promise((resolve,reject)=>entry.file(resolve,reject)));}
    else if(entry.isDirectory&&(depth===0||recursive)){
      const reader=entry.createReader();let batch;
      do{batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));for(const child of batch)await visit(child,depth+1);}while(batch.length);
    }
  }
  for(const entry of entries)await visit(entry,0);
  if(!files.length)throw new Error('所选文件夹中没有找到支持的视频');return files;
}
async function handleBrowserFiles(files,folder=false){if(folder)files=files.filter(f=>localVideoExtension.test(f.name));if(!files.length){toast('没有找到支持的视频文件',true);return;}const errors=[];for(const file of files){try{await addBrowserFile(file);}catch(e){errors.push(`${file.name}：${e.message}`);}}if(errors.length){for(const host of document.querySelectorAll('.local-source-panel'))host.querySelector('[data-source-note]').textContent=errors.join('\n');toast(`${errors.length} 个文件未能加入，其余视频已保留。${errors[0]}`,true);}else toast(`已导入 ${files.length} 个视频，请确认列表后开始处理`);}
async function addBrowserFile(file,id){
  const hash=await fingerprint(file);
  if(!id){const duplicate=localSources.find(s=>s.kind==='browser'&&s.name===file.name&&s.size===file.size&&s.fingerprint===hash);if(duplicate)id=duplicate.id;}
  const source=await api('/api/sources/browser',{method:'POST',body:{source_id:id,name:file.name,size:file.size,modified:file.lastModified,fingerprint:hash}});
    if(sourceSockets.get(source.id)?.readyState===WebSocket.OPEN){appendSources([source]);return;}
  detachSource(source.id);browserFiles.set(source.id,file);appendSources([source]);await connectSource(source.id);
}
async function connectSource(id){
  const file=browserFiles.get(id);if(!file)return;
  const previous=sourceSockets.get(id);if(previous&&previous.readyState<2)return;
  const socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/sources/${id}/read`);
  sourceSockets.set(id,socket);socket.binaryType='arraybuffer';
  await new Promise((resolve,reject)=>{
    let ready=false;
    socket.onopen=()=>socket.send(JSON.stringify({token}));
    socket.onmessage=async event=>{
      try{const request=JSON.parse(event.data);
        if(request.ready){ready=true;const source=localSources.find(s=>s.id===id);if(source){source.status='ready';source.error='';}renderLocalSources();api(`/api/sources/${id}/validate`,{method:'POST'}).catch(()=>{});resolve();return;}
        if(request.read){if(request.length>1048576)throw new Error('读取块超过限制');const part=await file.slice(request.offset,request.offset+request.length).arrayBuffer();const bytes=new Uint8Array(32+part.byteLength);bytes.set(new TextEncoder().encode(request.read));bytes.set(new Uint8Array(part),32);if(socket.readyState===WebSocket.OPEN&&sourceSockets.get(id)===socket)socket.send(bytes);}
      }catch(e){socket.close();if(!ready)reject(e);}
    };
    socket.onerror=()=>{if(!ready)reject(new Error('文件读取连接失败，请点击重连或重新选择文件'));};
    socket.onclose=()=>{if(sourceSockets.get(id)!==socket)return;sourceSockets.delete(id);const source=localSources.find(s=>s.id===id);if(source){source.status='waiting_source';source.error='连接中断，重新选择文件后点击继续';renderLocalSources();}if(!ready)reject(new Error('文件读取会话已断开，请重连服务后重新选择'));};
  });
}
async function sourcePreview(id,host){
  const v=host.querySelector('video'),cover=host.querySelector('[data-source-cover]'),note=host.querySelector('[data-source-note]');
  const info=await api(`/api/sources/${id}/preview`,{method:'POST'});const f=browserFiles.get(id);
  if(v.dataset.objectUrl)URL.revokeObjectURL(v.dataset.objectUrl);
  if(f){v.src=URL.createObjectURL(f);v.dataset.objectUrl=v.src;}else v.src=info.url;
  v.hidden=false;cover.hidden=true;
  note.textContent='加载预览中…';
  v.onloadedmetadata=()=>{note.textContent='预览已加载；原文件不会被修改。';};
  v.onerror=()=>{v.hidden=true;cover.src=info.cover;cover.hidden=false;note.textContent='浏览器无法播放此编码，已尝试封面预览；FFmpeg 可读取时仍能处理，可随时重新选择。';};
  cover.onerror=()=>{cover.hidden=true;note.textContent='封面也无法读取，请检查视频或重新选择；选择按钮仍可使用。';};
  if(!v.src)v.onerror();
}
function mountExportPanel(host){
  const kind=host.dataset.exportKind||(host.id==='extractExportChoices'?'rules':host.id==='analysisExportChoices'?'board':'export');
  host.dataset.workflow=kind==='export'?'board':kind;host.classList.add('export-options');
  const choices=kind==='rules'?[['frames','单帧图片'],['audio','音轨'],['clips','分段小视频'],['zip','另外生成 ZIP']]:kind==='board'?[['frames','额外单帧图片'],['audio','另存音轨'],['video','原视频（本机 / 已缓存）'],['zip','另外生成上传 ZIP']]:kind==='subtitles'?[['zip','另外生成 ZIP']]:[['frames','单帧图片'],['audio','音轨'],['puzzles','拼图'],['transcript','台词'],['clips','分段小视频'],['ai','AI 分析索引'],['zip','另外生成 ZIP']];
  host.innerHTML=`<h3>${kind==='board'?'AI 资料自动集中到“一起上传给AI”':kind==='subtitles'?'自动生成字幕区域截图长图':'导出哪些内容'}</h3>${kind==='board'?'<p class="mini-note">分镜、完整台词、时间索引和必要细节自动生成；附加素材保存在上传文件夹外。</p>':''}<div class="output-checks">${choices.map(([k,v])=>`<label class="output-card"><input type="checkbox" data-setting="outputs.${k}"><span>${v}</span></label>`).join('')}</div>${['board','subtitles'].includes(kind)?'':`<label class="checkbox-line"><input type="checkbox" data-setting="dedup.enabled">导出图片去重</label><div class="inline-fields"><label class="field">去重强度<select data-dedup-preset><option value="exact">完全相同</option><option value="4">保守 · 4</option><option value="8">适中 · 8</option><option value="12">宽松 · 12</option><option value="custom">自定义</option></select></label><label class="field">自定义阈值 0–16<input type="number" min="0" max="16" data-setting="dedup.threshold"></label></div><p class="mini-note">阈值越大，越可能合并字幕或细节变化。</p><label class="checkbox-line"><input type="checkbox" data-setting="segments.enabled">开启时间分段</label><div class="inline-fields"><label class="field">每段秒数<input type="number" min="0.1" max="86400" step="0.1" data-setting="segments.seconds"></label><label class="checkbox-line"><input type="checkbox" data-setting="segments.organize">按段整理素材</label></div>`}<label class="field">处理速度<select data-performance><option value="auto">自动 · 根据 CPU 与可用内存</option><option value="low">低占用</option><option value="balanced">均衡</option><option value="high">高性能</option></select><small>仅调整资源使用，保留相同画面、文字与必要细节。</small></label>`;
  host.addEventListener('change',e=>{
    const state=settingsFor(host);
    if(e.target.dataset.setting){const [section,key]=e.target.dataset.setting.split('.');state[section]??={};state[section][key]=e.target.type==='checkbox'?e.target.checked:Number(e.target.value);if(key==='clips'&&e.target.checked)state.segments.enabled=true;if(key==='enabled'&&section==='segments'&&!e.target.checked)state.outputs.clips=false;}
    if(e.target.hasAttribute('data-dedup-preset')){const value=e.target.value;state.dedup.preset=value;state.dedup.mode=value==='exact'?'exact':'similar';if(value!=='exact'&&value!=='custom')state.dedup.threshold=Number(value);}
    if(e.target.hasAttribute('data-performance'))state.performance=e.target.value;
    syncExportPanels();
  });
  host.addEventListener('input',e=>{if(e.target.dataset.setting&&e.target.type==='number'&&e.target.value!==''){const [section,key]=e.target.dataset.setting.split('.');settingsFor(host)[section][key]=Number(e.target.value);saveLocalState();}});
}
function syncExportPanels(){
  for(const panel of document.querySelectorAll('.export-options')){
    const state=settingsFor(panel);if(!state)continue;
    panel.querySelectorAll('[data-setting]').forEach(el=>{const [s,k]=el.dataset.setting.split('.');if(el.type==='checkbox')el.checked=!!state[s]?.[k];else el.value=state[s]?.[k]??'';});
    panel.querySelectorAll('[data-performance]').forEach(el=>el.value=state.performance||'auto');
    panel.querySelectorAll('[data-dedup-preset]').forEach(el=>{const d=state.dedup;el.value=d.mode==='exact'?'exact':d.preset==='custom'?'custom':[4,8,12].includes(d.threshold)?String(d.threshold):'custom';el.disabled=!d.enabled;});
  }
  if(typeof updateWorkflowStartLabels==='function')updateWorkflowStartLabels();
  saveLocalState();
}
function readExportSettings(panel){
  const state=settingsFor(panel);if(!panel)return state;
  for(const el of panel.querySelectorAll('[data-setting]')){const [section,key]=el.dataset.setting.split('.');state[section]??={};state[section][key]=el.type==='checkbox'?el.checked:Number(el.value);}
  return state;
}
async function submitLocalBatch(kind,analysis={}){
  activateWorkflow(kind);
  if(kind==='board'&&typeof regenerationAnalysis==='function')analysis={...regenerationAnalysis(),...analysis};
  readExportSettings(document.querySelector(kind==='rules'?'#page-extract .export-options':'#page-analysis .export-options'));
  if(!localSources.length)throw new Error('请先拖入、选择文件 / 文件夹，或粘贴路径');
  const usable=localSources.filter(s=>s.status!=='invalid');
  if(!usable.length)throw new Error('列表中的视频均不可读，请重新选择');
  const body={sources:usable.map(s=>s.id),output:$('output').value,kind,workflow:kind,performance:exportSettings.performance,outputs:exportSettings.outputs,dedup:exportSettings.dedup,segments:exportSettings.segments,ai_package:exportSettings.ai_package,frames:frameSettings(),analysis};
  const signature=JSON.stringify(body);
  if(!batchRequest||batchRequest.signature!==signature)batchRequest={signature,id:crypto.randomUUID()};
  body.request_id=batchRequest.id;saveLocalState();
  const result=await api('/api/batches',{method:'POST',body});lastBatch=result.id;saveLocalState();await refresh();goto('library');toast(`批次已建立，${usable.length} 个视频串行处理；每个视频独立保存。`);
}
const analysisHost=document.createElement('div');analysisHost.id='analysisLocalSources';analysisHost.className='local-source-panel';analysisHost.hidden=$('analysisSource').value!=='local';$('analysisInputField').after(analysisHost);mountSourcePanel(analysisHost,'analysis');
const extractHost=document.createElement('div');extractHost.className='local-source-panel';$('extractLocalPanel').prepend(extractHost);mountSourcePanel(extractHost,'extract');
// Keep the old crop preview available after selecting a source from the list.
$('localVideoPath').closest('label').hidden=true;$('pickVideo').hidden=true;
$('previewLocalPath').textContent='加载首个路径视频 / 框选区域';
$('previewLocalPath').addEventListener('pointerdown',()=>{const first=localSources.find(s=>s.kind==='path');if(first)$('localVideoPath').value=first.path;});
for(const [id,anchor] of [['analysisExportChoices',$('analysisKeepFrames').closest('label')],['extractExportChoices',$('extractAudio').closest('label')]]){const panel=document.createElement('div');panel.id=id;anchor.before(panel);mountExportPanel(panel);}
// Existing online and saved-video operations retain their own controls. The
// selected-output queue applies to local batches; all completed jobs can also
// use the common export dialog below.
const updateAnalysisSourceBefore21=updateAnalysisSource;
updateAnalysisSource=function(){updateAnalysisSourceBefore21();const local=$('analysisSource').value==='local';$('analysisExportChoices').hidden=$('analysisSource').value==='job';$('analysisKeepFrames').closest('label').hidden=true;$('analysisAudio').closest('label').hidden=true;};
$('analysisSource').addEventListener('change',()=>updateAnalysisSource());
const updateExtractModeBefore21=updateExtractMode;
updateExtractMode=function(){updateExtractModeBefore21();const local=$('extractMode').value==='local';$('extractExportChoices').hidden=false;$('extractAudio').closest('label').hidden=true;};
$('extractMode').addEventListener('change',()=>updateExtractMode());updateExtractMode();
const reconnect=document.createElement('button');reconnect.className='text-btn';reconnect.textContent='重连';reconnect.onclick=()=>busy(reconnect,async()=>{await refresh();for(const [id,file] of [...browserFiles])await addBrowserFile(file,id);toast('本机服务已连接，选择和设置已保留');}).catch(e=>toast(e.message,true));$('connectionBadge').append(reconnect);
const previousRender21=renderJobs;
renderJobs=function(){previousRender21();document.querySelectorAll('#jobs .job').forEach(article=>{const id=article.querySelector('[data-id]')?.dataset.id,j=jobs.find(x=>x.id===id);if(!j||!['batch','batch-video'].includes(j.kind))return;const actions=article.querySelector('.job-actions');if(j.kind==='batch-video'&&j.status==='waiting_source'){const choose=document.createElement('button');choose.className='subtle';choose.textContent='重新选择文件并继续';choose.onclick=()=>{sourceReattachInput.dataset.sourceId=j.options.source_id;sourceReattachInput.dataset.jobId=id;sourceReattachInput.click();};actions.append(choose);}if(['error','cancelled','interrupted','waiting_source','waiting'].includes(j.status)){const button=document.createElement('button');button.className='subtle';button.textContent='重试 / 从未完成片段继续';button.onclick=()=>api(`/api/batches/${id}/retry`,{method:'POST'}).then(refresh).catch(e=>toast(e.message,true));actions.append(button);}if(j.kind==='batch-video'){const view=document.createElement('button');view.className='subtle';view.textContent='查看分镜与台词';view.onclick=()=>openAnalysis(id).catch(e=>toast(e.message,true));actions.append(view);}if(j.status==='done'){const exp=document.createElement('button');exp.className='text-btn';exp.textContent='选择内容再导出';exp.onclick=()=>openCommonExport(id);actions.append(exp);}if(j.kind==='batch'&&j.counts){const counts=document.createElement('small');counts.textContent=`成功 ${j.counts.done} / 失败 ${j.counts.error} / 等待来源 ${j.counts.waiting_source}`;actions.append(counts);}});};
let sourcePoll=false;
setInterval(async()=>{if(sourcePoll||!localSources.length)return;sourcePoll=true;try{const result=await api('/api/sources');const byId=new Map(result.sources.map(s=>[s.id,s]));let changed=false;for(let i=0;i<localSources.length;i++){const source=byId.get(localSources[i].id);if(source&&JSON.stringify(source)!==JSON.stringify(localSources[i])){localSources[i]=source;changed=true;}}if(changed)renderLocalSources();}catch{}finally{sourcePoll=false;}},2500);
renderLocalSources();syncExportPanels();updateAnalysisSource();
// The EXE may reopen on another loopback port. Persist preferences in the app
// data directory as well as this origin's localStorage; File bytes stay in-page.
api('/api/batch-settings').then(saved=>{if(saved.workflows&&(!savedAt25||savedAt25<=(saved.saved_at||0))){migrateWorkflows(saved);localSources=saved.sources||localSources;lastBatch=saved.batch||'';batchRequest=saved.pending||null;}serverSettingsLoaded=true;renderLocalSources();syncExportPanels();if(typeof restoreWorkflowFields==='function')restoreWorkflowFields();saveLocalState();}).catch(e=>toast('读取已保存设置失败：'+e.message,true));

window.addEventListener('beforeunload',event=>{if(jobs.some(j=>j.kind==='batch-video'&&activeStatus(j.status)&&browserFiles.has(j.options?.source_id))){event.preventDefault();event.returnValue='';}});
async function pickLegacy21(kind){
  if(pickerActive)throw new Error('已有选择窗口打开，请先完成或取消');
  const notice=document.createElement('div');notice.className='picker-notice';notice.innerHTML='<span>正在等待文件选择，可随时取消。</span><button class="subtle">取消选择</button>';document.body.append(notice);
  try{const task=await api('/api/pickers',{method:'POST',body:{kind}});pickerActive=task.id;notice.querySelector('button').onclick=()=>api(`/api/pickers/${task.id}/cancel`,{method:'POST'}).catch(e=>toast(e.message,true));let result;
    do{await new Promise(r=>setTimeout(r,500));result=await api(`/api/pickers/${task.id}`);}while(result.status==='pending');
    if(result.status==='error')throw new Error(result.error);return {path:result.paths?.[0]||''};
  }finally{pickerActive=null;notice.remove();}
}
let commonExportId='',exportRequestId='',exportSignature='';
const exportDialog=document.createElement('dialog');exportDialog.id='commonExportDialog';exportDialog.innerHTML='<div class="panel-head"><h2>选择要导出的内容</h2><button class="text-btn" data-export-close>关闭 ×</button></div><div class="padded"><p>从原来源按所选内容重新生成，保存到顶部设置的位置。已有素材保留。</p><div data-export-controls></div><button class="primary wide" data-export-submit>开始导出</button></div>';document.body.append(exportDialog);mountExportPanel(exportDialog.querySelector('[data-export-controls]'));syncExportPanels();
exportDialog.querySelector('[data-export-close]').onclick=()=>exportDialog.close();
function openCommonExport(id){const options=jobs.find(j=>j.id===id)?.options||{},kind=options.workflow||'export';dialogSettings=structuredClone({...workflowSettings[kind]||workflowSettings.board,...options});const old=exportDialog.querySelector('[data-export-controls]'),panel=old.cloneNode(false);panel.dataset.exportKind=kind;old.replaceWith(panel);mountExportPanel(panel);commonExportId=id;exportRequestId=crypto.randomUUID();exportSignature='';syncExportPanels();exportDialog.showModal();}
exportDialog.querySelector('[data-export-submit]').onclick=()=>busy(exportDialog.querySelector('[data-export-submit]'),async()=>{const dialogState=readExportSettings(exportDialog.querySelector('.export-options'));const signature=JSON.stringify({id:commonExportId,output:$('output').value,...dialogState});if(exportSignature&&exportSignature!==signature)exportRequestId=crypto.randomUUID();exportSignature=signature;await api(`/api/exports/${commonExportId}`,{method:'POST',body:{...dialogState,output:$('output').value,request_id:exportRequestId}});exportDialog.close();await refresh();goto('library');toast('导出任务已建立');}).catch(e=>toast(e.message,true));
const gotoBefore21=goto;
goto=function(page){if(['analysis','extract','subtitles'].includes(page))activateWorkflow({analysis:'board',extract:'rules',subtitles:'subtitles'}[page]);return gotoBefore21(page);};
const commonExportButton=document.createElement('button');commonExportButton.className='subtle';commonExportButton.textContent='自选内容导出';commonExportButton.onclick=()=>openCommonExport(analysisId);$('analysisExport').after(commonExportButton);

const sourceReattachInput=document.createElement('input');sourceReattachInput.type='file';sourceReattachInput.accept='video/*,.mkv,.flv,.ts,.m2ts';sourceReattachInput.hidden=true;document.body.append(sourceReattachInput);
sourceReattachInput.onchange=async()=>{const file=sourceReattachInput.files[0],sid=sourceReattachInput.dataset.sourceId,jid=sourceReattachInput.dataset.jobId;sourceReattachInput.value='';if(!file)return;try{await addBrowserFile(file,sid);await api(`/api/batches/${jid}/retry`,{method:'POST'});await refresh();toast('文件已核对，从未完成片段继续');}catch(e){toast(e.message,true);}};
async function previewSelected21(){
  const source=localSources.find(s=>s.status==='ready')||localSources[0];if(!source)throw new Error('请先选择视频');
  if(source.kind==='path'){$('localVideoPath').value=source.path;return previewLocalPath();}
  const file=browserFiles.get(source.id);if(!file)throw new Error('请重新选择此网页文件，再加载预览');
  if(localObjectUrl)URL.revokeObjectURL(localObjectUrl);localObjectUrl=URL.createObjectURL(file);localRegion=null;localFrame=null;localVideo.src=localObjectUrl;localVideo.hidden=false;
  $('extractSource').textContent='正在直接预览网页文件，加载后可框选区域。';
  localVideo.onerror=()=>{$('localCropWrap').hidden=true;$('extractSource').textContent='浏览器无法预览此编码，仍可用 FFmpeg 处理。请使用来源列表的封面预览，或重新选择文件。';};
}
