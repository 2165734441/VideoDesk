'use strict';
function jobProgress(j){
  const active=['queued','running','processing','waiting','waiting_source','recording'].includes(j.status);
  if(!active&&j.status!=='done')return '';
  const percent=j.status==='done'?100:Math.max(0,Math.min(99,Number(j.progress)||0));
  const waiting=['waiting','waiting_source'].includes(j.status),pending=j.status==='queued';
  const unknown=!!j.progress_indeterminate||pending||j.status==='recording';
  const d=j.progress_detail||{},parts=[];
  const bytes=n=>n>=1048576?`${(n/1048576).toFixed(1)} MB`:`${Math.round(n/1024)} KB`;
  if(d.downloaded_bytes){parts.push(`${bytes(d.downloaded_bytes)}${d.total_bytes?' / '+bytes(d.total_bytes):''}`);}
  if(d.speed)parts.push(`${bytes(d.speed)}/秒`);
  if(Number.isFinite(d.completed)&&Number.isFinite(d.total))parts.push(`本步骤 ${d.completed} / ${d.total}`);
  if(Number.isFinite(d.elapsed))parts.push(`本步骤耗时 ${d.elapsed} 秒`);
  if(Number.isFinite(d.eta)&&d.eta>0)parts.push(`本阶段剩余约 ${Math.ceil(d.eta)} 秒`);
  const label=waiting?(j.status==='waiting_source'?'等待来源 · 重新选择并继续':'等待网站验证 · 手动处理后继续'):pending?'排队中':j.status==='recording'?'正在录制':unknown?'正在处理 · 当前阶段时长待确定':j.status==='done'?'已完成 100%':`总进度约 ${percent}%`;
  return `<div class="task-progress ${unknown&&!waiting?'is-indeterminate':''} ${waiting?'is-paused':''}"><div class="task-progress-label"><strong>${html(label)}</strong><span>${html(parts.join(' · '))}</span></div><div class="progress" role="progressbar" aria-label="任务总进度" aria-valuemin="0" aria-valuemax="100" ${unknown?'':`aria-valuenow="${percent}"`}><span style="width:${unknown?35:percent}%"></span></div></div>`;
}
const $ = id => document.getElementById(id);
let token = '', jobs = [], currentPage = 'import';
let sourceStream = null, region = null, localRegion = null, localObjectUrl = null, savedSource = null;
let session = null, finishing = false, rendering = false;
const video = $('sourceVideo'), preview = $('preview');
const previewCtx = preview.getContext('2d');
const outputCanvas = document.createElement('canvas'), outputCtx = outputCanvas.getContext('2d', {alpha: false});
const statusLabels = {queued:'排队中',running:'处理中',waiting:'待人工验证',waiting_source:'等待来源',recording:'采集中',processing:'正在保存',done:'已完成',error:'失败',cancelled:'已取消',interrupted:'已中断'};
const html = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const activeStatus = s => ['queued','running','recording','processing','waiting','waiting_source'].includes(s);
function toast(message, error = false) {
  $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, error ? 10000 : 5500);
}
async function api(url, {method = 'GET', body, raw = false, timeoutMs} = {}) {
  if (method==='POST' && typeof pickLegacy21==='function' && ['/api/pick-directory','/api/pick-video'].includes(url))return pickLegacy21(url.endsWith('directory')?'folder':'files');
  const headers = {};
  if (method !== 'GET') headers['X-App-Token'] = token;
  if (body !== undefined && !raw) {headers['Content-Type'] = 'application/json'; body = JSON.stringify(body);}
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(), timeoutMs??(method==='GET'?5000:15000));
  try {
    const response = await fetch(url, {method, headers, body, signal:controller.signal});
    const data = await response.json().catch(e=>{if(e.name==='AbortError')throw e;return {};});
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : response.status===422?'参数不正确，请检查数量、时间和所选内容。':`请求失败（${response.status}），请检查参数。`);
      error.status=response.status;throw error;
    }
    return data;
  } catch(e) {
    if(e.status)throw e;
    const error=new Error(e.name==='AbortError'?'本机请求超时，请重试；选择和设置已保留。':'本机服务连接已断开。请点击重连；若地址已失效，请从系统托盘重新打开工作台。');error.connection=true;throw error;
  } finally {clearTimeout(timer);}
}
function bind(id, fn) {$(id).addEventListener('click', async () => {const b=$(id);if(b.dataset.pending==='1')return;b.dataset.pending='1';b.setAttribute('aria-busy','true');try{await fn();}catch(e){toast(e.message,true);}finally{delete b.dataset.pending;b.removeAttribute('aria-busy');}});}
async function busy(button, fn) {
  button.disabled = true; const label = button.textContent; button.textContent = '处理中…';
  try {return await fn();} finally {button.disabled = false; button.textContent = label;}
}
function goto(page) {
  currentPage = page;
  document.querySelectorAll('.page').forEach(el => el.hidden = el.id !== `page-${page}`);
  const parent=({capture:'import',download:'import',crawler:'import',dedup:'library'}[page])||page;
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.page === parent));
  $('breadcrumb').textContent = '工作空间 / ' + ({live:'直播录播与回放',translate:'英文视频转中文',import:'导入与采集',analysis:'AI 看懂视频',subtitles:'字幕区域截图长图',settings:'账号与模型',capture:'网页录制与截图',download:'链接下载',crawler:'平台采集',extract:'视频抽帧',dedup:'照片与截图去重',library:'素材库'}[page]);
  if (page === 'library') renderJobs();
  if (page === 'dedup' && typeof refreshDedup === 'function') refreshDedup().catch(e=>toast(e.message,true));
  if (typeof onWorkflowPage==='function') onWorkflowPage(page);
  window.scrollTo(0,0);
}
document.querySelectorAll('[data-page]').forEach(el => el.addEventListener('click', () => goto(el.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(el => el.addEventListener('click', () => goto(el.dataset.goto)));
async function refresh() {
  const state = await api('/api/state');if(state.application!=='video-desk')throw new Error('此地址已失效，请从系统托盘重新打开映格工作台。'); token = state.token; jobs = state.jobs;
  if(typeof window!=='undefined'&&typeof CustomEvent!=='undefined')window.dispatchEvent(new CustomEvent('video-desk-state',{detail:state}));
  $('connectionText').textContent='本地服务已连接';$('connectionBadge').classList.remove('disconnected');
  if (!$('output').value) $('output').value = state.settings.output;
  $('jobCount').textContent = jobs.length;
  if (currentPage === 'library') renderJobs();
  if ($('youtubeStatus') && state.youtube) $('youtubeStatus').textContent=state.youtube.ready?'YouTube 组件已就绪':'YouTube 需 Node.js 22+ 与 yt-dlp[default]';
  if ($('engineStatus') && state.engines) $('engineStatus').textContent = state.engines.mediacrawler ? '双引擎已安装' : 'MediaCrawler 环境待安装';
  if (typeof refreshDedup === 'function') {
    try {await refreshDedup();}
    catch (error) {$('dedupStatus').textContent = '去重结果读取失败：' + error.message;}
  }
  return state;
}
bind('pickDir', () => busy($('pickDir'), async () => {const result = await api('/api/pick-directory', {method:'POST'}); if (result.path) {$('output').value = result.path; await saveDirectory();}}));
async function saveDirectory() {await api('/api/settings', {method:'POST',body:{output:$('output').value}}); toast('保存位置已更新');}
bind('saveDir', saveDirectory);

// Draw the selection over the preview only. Saved frames never contain the overlay.
function drawSelection(ctx, crop, width, height) {
  if (!crop) return;
  const [x,y,w,h] = crop.map((v,i) => v * (i % 2 ? height : width));
  ctx.fillStyle = '#07161977';
  ctx.fillRect(0,0,width,y); ctx.fillRect(0,y+h,width,height-y-h);
  ctx.fillRect(0,y,x,h); ctx.fillRect(x+w,y,width-x-w,h);
  ctx.strokeStyle = '#78ddbf'; ctx.lineWidth = Math.max(2, width / 500); ctx.strokeRect(x,y,w,h);
}
function makeCrop(canvas, getRegion, setRegion, draw, isLocked = () => false) {
  let start = null;
  function point(event) {
    const box = canvas.getBoundingClientRect();
    // object-fit:contain may add letterboxing inside the canvas element.
    const scale = Math.min(box.width / canvas.width, box.height / canvas.height);
    const w = canvas.width * scale, h = canvas.height * scale;
    return [Math.max(0,Math.min(1,(event.clientX-box.left-(box.width-w)/2)/w)),
            Math.max(0,Math.min(1,(event.clientY-box.top-(box.height-h)/2)/h))];
  }
  canvas.addEventListener('pointerdown', event => {
    if (isLocked()) return;
    start = point(event); canvas.setPointerCapture(event.pointerId); event.preventDefault();
  });
  canvas.addEventListener('pointermove', event => {
    if (!start) return;
    const end = point(event); setRegion([Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(start[0]-end[0]),Math.abs(start[1]-end[1])]); draw();
  });
  function finish() {if (!start) return; start = null; const c = getRegion(); if (!c || c[2]<.01 || c[3]<.01) setRegion(null); draw();}
  canvas.addEventListener('pointerup', finish); canvas.addEventListener('pointercancel', finish);
}
function drawOutput() {
  if (!video.videoWidth) return false;
  const [x,y,w,h] = region || [0,0,1,1];
  const width = Math.max(2,Math.round(w * video.videoWidth)), height = Math.max(2,Math.round(h * video.videoHeight));
  if (outputCanvas.width !== width || outputCanvas.height !== height) {outputCanvas.width = width; outputCanvas.height = height;}
  outputCtx.drawImage(video, x*video.videoWidth,y*video.videoHeight,w*video.videoWidth,h*video.videoHeight,0,0,width,height);
  return true;
}
function drawPreview() {
  if (!video.videoWidth) return;
  if (preview.width !== video.videoWidth || preview.height !== video.videoHeight) {preview.width=video.videoWidth;preview.height=video.videoHeight;}
  previewCtx.drawImage(video,0,0,preview.width,preview.height);
  drawSelection(previewCtx,region,preview.width,preview.height);
  const crop = region || [0,0,1,1];
  $('resolution').textContent = `${video.videoWidth} × ${video.videoHeight} · 保存区域 ${Math.round(crop[2]*video.videoWidth)} × ${Math.round(crop[3]*video.videoHeight)}`;
}
function startRender() {
  if (rendering) return; rendering = true;
  // Keep an explicit timer for the recorder, independent from animation frames.
  const timer = setInterval(() => {if (!sourceStream) {clearInterval(timer);rendering=false;return;} drawOutput();drawPreview();}, 1000/30);
}
makeCrop(preview,()=>region,c=>region=c,drawPreview,()=>!!session);
function updateSourceButtons() {
  const ready = !!sourceStream;
  $('selectSource').disabled = !!session; $('replaceSource').disabled = !ready || !!session;
  $('resetCrop').disabled = !ready || !!session; $('releaseSource').disabled = !ready || !!session;
  $('startCapture').disabled = !ready || !!session;
}
async function selectSource() {
  if (session) throw new Error('请先停止并保存当前采集。');
  if (!navigator.mediaDevices?.getDisplayMedia) throw new Error('请在最新版 Chrome / Edge 中打开本机地址，以使用标签页共享。');
  const stream = await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:30,max:60}},audio:true,
    selfBrowserSurface:'exclude',surfaceSwitching:'include',systemAudio:'include'});
  if (sourceStream) sourceStream.getTracks().forEach(t=>t.stop());
  sourceStream=stream;region=null;video.srcObject=stream;await video.play();
  $('previewEmpty').hidden=true;preview.hidden=false;
  $('captureBadge').textContent='来源已连接';$('captureBadge').className='badge';
  $('audioState').textContent=stream.getAudioTracks().length?'标签页':'无音轨';
  stream.getVideoTracks()[0].addEventListener('ended', async () => {
    if (sourceStream!==stream) return;
    if (session && !finishing) {toast('来源共享已停止，正在保存已采集素材。');await stopCapture().catch(e=>toast(e.message,true));}
    sourceStream=null;video.srcObject=null;preview.hidden=true;$('previewEmpty').hidden=false;updateSourceButtons();
  });
  startRender();updateSourceButtons();
}
bind('selectSource',selectSource);bind('replaceSource',selectSource);
bind('resetCrop',()=>{region=null;drawPreview();});
bind('releaseSource',()=>{if(session) return;sourceStream?.getTracks().forEach(t=>t.stop());sourceStream=null;video.srcObject=null;preview.hidden=true;$('previewEmpty').hidden=false;$('resolution').textContent='尚未连接画面';$('audioState').textContent='—';$('captureBadge').textContent='等待选择来源';updateSourceButtons();});
function frequency() {
  const value=Number($('shotValue').value),mode=$('shotMode').value;
  if (!(value>0) || (mode==='fps' && value>10) || (mode==='seconds' && value<.1)) throw new Error('实时截图支持每秒最多 10 张，或最短每 0.1 秒一张。');
  return mode==='fps'?1/value:value;
}
function frequencyHelp() {$('frequencyHelp').textContent=$('shotMode').value==='fps'?`每秒保存 ${$('shotValue').value} 张图片`:`每 ${$('shotValue').value} 秒保存 1 张图片`;}
$('shotMode').addEventListener('change',frequencyHelp);$('shotValue').addEventListener('input',frequencyHelp);
function elapsed(s=session) {return s?(performance.now()-s.start-s.pauseTotal-(s.paused?performance.now()-s.pauseAt:0))/1000:0;}
function clock(seconds) {return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;}
async function reliableUpload(url, blob) {
  let last;
  for (let attempt=0;attempt<3;attempt++) {
    try {return await api(url,{method:'POST',body:blob,raw:true});}
    catch(e) {last=e;if(attempt<2) await new Promise(r=>setTimeout(r,500*(attempt+1)));}
  }
  throw last;
}
function enqueueUpload(s,url,blob,onSuccess) {
  s.pending++;
  s.chain=s.chain.then(async()=>{
    try {await reliableUpload(url,blob);onSuccess?.();}
    catch(e) {s.failed.push({url,blob,onSuccess});toast('素材上传失败，数据暂存于网页内存，请保持网页打开，停止后点击重试。',true);}
    finally {s.pending--;}
  });
}
async function takeShot(s) {
  if (!s || s.shotBusy || s.paused || finishing || s.shots>=s.limit) return;
  s.shotBusy=true;
  try {
    drawOutput();const seconds=elapsed(s),index=s.shots++;
    const blob=await new Promise(resolve=>outputCanvas.toBlob(resolve,'image/jpeg',.91));
    if (!blob) throw new Error('截图失败');
    enqueueUpload(s,`/api/capture/${s.id}/shot?index=${index}&seconds=${seconds.toFixed(3)}`,blob,()=>{$('shotCount').textContent=++s.saved;});
  } catch(e) {toast(e.message,true);} finally {s.shotBusy=false;}
}
function lockCaptureFields(locked) {document.querySelectorAll('.controls-panel input,.controls-panel select').forEach(e=>e.disabled=locked);updateSourceButtons();}
async function startCapture() {
  if (!sourceStream || session) return;
  if (!$('recordVideo').checked && !$('takeShots').checked) throw new Error('请至少选择录制视频或定时截图。');
  const interval=frequency(),duration=Number($('stopAfter').value),limit=Number($('shotLimit').value);
  if (!Number.isInteger(limit)||limit<1||limit>100000||!Number.isFinite(duration)||duration<0||duration>86400) throw new Error('请检查自动停止时间与截图上限。');
  const shouldRecord=$('recordVideo').checked;
  if (shouldRecord && !window.MediaRecorder) throw new Error('此浏览器不支持录制，请改用最新版 Chrome / Edge。');
  drawOutput();
  const result=await api('/api/capture',{method:'POST',body:{title:$('captureTitle').value||'网页采集',source:$('sourceUrl').value,
    output:$('output').value,options:{crop:region,interval,record:shouldRecord,shots:$('takeShots').checked,audio:sourceStream.getAudioTracks().length>0}}});
  const s={id:result.id,start:performance.now(),pauseTotal:0,pauseAt:0,paused:false,interval,duration,limit,shots:0,saved:0,
    chunks:0,chain:Promise.resolve(),failed:[],pending:0,takeShots:$('takeShots').checked,nextShot:0,mp4:$('convertMp4').checked};
  session=s;finishing=false;$('shotCount').textContent='0';
  try {
    if (shouldRecord) {
      s.canvasStream=outputCanvas.captureStream(30);
      s.recordStream=new MediaStream([...s.canvasStream.getVideoTracks(),...sourceStream.getAudioTracks()]);
      const mime=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm'].find(t=>MediaRecorder.isTypeSupported(t));
      if (!mime) throw new Error('当前浏览器没有可用的 WebM 录制编码器。');
      s.recorder=new MediaRecorder(s.recordStream,{mimeType:mime,videoBitsPerSecond:5000000});
      s.recorder.ondataavailable=event=>{if(event.data.size) {const index=s.chunks++;enqueueUpload(s,`/api/capture/${s.id}/chunk?index=${index}`,event.data);}};
      s.recorder.onerror=()=>{toast('录制器遇到错误，正在保存已取得的素材。',true);stopCapture().catch(e=>toast(e.message,true));};
      s.recorder.start(2000);
    }
    s.timer=setInterval(()=>{
      const seconds=elapsed(s);$('elapsed').textContent=clock(seconds);
      if (!s.paused && s.takeShots && seconds>=s.nextShot && !s.shotBusy) {s.nextShot=seconds+s.interval;takeShot(s);}
      if (s.duration>0 && seconds>=s.duration && !finishing) stopCapture().catch(e=>toast(e.message,true));
    },40);
    $('captureBadge').textContent='● 正在采集';$('captureBadge').className='badge recording';
    $('pauseCapture').disabled=false;$('stopCapture').disabled=false;lockCaptureFields(true);
    toast(sourceStream.getAudioTracks().length?'采集已开始，画面与标签页声音会一起保存。':'采集已开始。当前没有音轨；需要解说声音时请重新共享并勾选音频。');
  } catch(e) {await stopCapture().catch(()=>{});throw e;}
}
bind('startCapture',()=>busy($('startCapture'),startCapture).finally(updateSourceButtons));
bind('pauseCapture',()=>{
  const s=session;if(!s||finishing) return;
  if (!s.paused) {s.paused=true;s.pauseAt=performance.now();if(s.recorder?.state==='recording') s.recorder.pause();$('pauseCapture').textContent='继续采集';$('captureBadge').textContent='已暂停 · 可手动验证';}
  else {s.pauseTotal+=performance.now()-s.pauseAt;s.paused=false;if(s.recorder?.state==='paused') s.recorder.resume();$('pauseCapture').textContent='暂停采集';$('captureBadge').textContent='● 正在采集';}
});
async function stopCapture() {
  const s=session;if(!s||finishing) return;finishing=true;
  clearInterval(s.timer);$('pauseCapture').disabled=true;$('stopCapture').disabled=true;$('captureBadge').textContent='正在保存素材…';
  if (s.recorder && s.recorder.state!=='inactive') {
    await new Promise(resolve=>{s.recorder.addEventListener('stop',resolve,{once:true});s.recorder.stop();});
  }
  while(s.shotBusy) await new Promise(resolve=>setTimeout(resolve,30));
  s.canvasStream?.getTracks().forEach(t=>t.stop());
  await s.chain;
  if (s.failed.length) {$('retryUploads').hidden=false;throw new Error(`有 ${s.failed.length} 份素材上传失败，请点击重试。不要关闭页面。`);}
  await finalizeCapture(s);
}
async function finalizeCapture(s) {
  try {await api(`/api/capture/${s.id}/finish`,{method:'POST',body:{chunks:s.chunks,mp4:s.mp4,audio:s.mp4}});}
  catch(e) {$('retryUploads').hidden=false;throw e;}
  session=null;finishing=false;$('retryUploads').hidden=true;
  $('pauseCapture').textContent='暂停采集';$('captureBadge').textContent='已提交保存';$('captureBadge').className='badge';
  lockCaptureFields(false);await refresh();toast('采集已提交保存。到素材与任务查看处理结果。');
}
bind('stopCapture',stopCapture);
bind('retryUploads',()=>busy($('retryUploads'),async()=>{
  const s=session;if(!s) return;
  const failed=s.failed.splice(0);
  for (const entry of failed) enqueueUpload(s,entry.url,entry.blob,entry.onSuccess);
  await s.chain;
  if(s.failed.length) throw new Error('仍有素材上传失败，请检查本机服务后再次重试。');
  await finalizeCapture(s);
}));
window.addEventListener('beforeunload',event=>{if(session) {event.preventDefault();event.returnValue='';}});
document.addEventListener('visibilitychange',()=>{$('visibilityWarning').hidden=!document.hidden||!session;if(document.hidden&&session&&!session.paused) toast('请将采集工作台保持可见，避免浏览器降低采集频率。',true);});

function frameSettings(prefix='') {
  const online=$('extractMode').value==='url';
  const crop=online?($('streamCropEnabled').checked?['X','Y','W','H'].map(k=>Number($('streamCrop'+k).value)/100):null):localRegion;
  return {mode:$(prefix+'frameMode').value,value:Number($(prefix+'frameValue').value),crop,
    start:Number($('frameStart').value),duration:Number($('frameDuration').value),limit:Number($('frameLimit').value),audio:$('extractAudio').checked};
}
bind('downloadBtn',()=>busy($('downloadBtn'),async()=>{
  const urls=$('downloadUrls').value.split('\n').map(x=>x.trim()).filter(Boolean);
  const result=await api('/api/download',{method:'POST',body:{urls,output:$('output').value,login:$('useLogin').checked,
    cookie_id:$('downloadCookie')?.value||'',
    extract:$('downloadExtract').checked,frames_only:$('downloadFramesOnly').checked,
    frames:{mode:$('downloadFrameMode').value,value:Number($('downloadFrameValue').value),audio:!$('downloadFramesOnly').checked||$('downloadFrameAudio').checked}}});
  toast(`已添加 ${result.ids.length} 个${$('downloadFramesOnly').checked?'直接抽帧':'下载'}任务`);await refresh();goto('library');
}));
$('downloadFramesOnly').addEventListener('change',()=>{
  const only=$('downloadFramesOnly').checked;
  $('downloadExtract').disabled=only;if(only)$('downloadExtract').checked=true;
  $('downloadAudioRow').hidden=!only;$('downloadBtn').textContent=only?'▦ 添加到直接抽帧队列':'↓ 添加到下载队列';
});
async function openLogin(url,button) {return busy(button,async()=>{const result=await api('/api/browser/open',{method:'POST',body:{url}});toast(result.message);});}
document.querySelectorAll('.login-btn').forEach(button=>button.addEventListener('click',()=>openLogin(button.dataset.url,button).catch(e=>toast(e.message,true))));
bind('openFirstUrl',()=>openLogin($('downloadUrls').value.split('\n').find(x=>x.trim())||'', $('openFirstUrl')));

const localVideo=$('localVideo'),localCanvas=$('localCrop'),localCtx=localCanvas.getContext('2d');
let localFrame=null;
function updateExtractMode() {
  const mode=$('extractMode').value;
  for(const [id,value] of [['extractLocalPanel','local'],['extractUrlPanel','url'],['extractUploadPanel','upload'],['extractSavedPanel','saved']])$(id).hidden=mode!==value;
  localVideo.pause();localVideo.hidden=true;$('localCropWrap').hidden=true;localRegion=null;localFrame=null;
  $('extractSource').hidden=mode==='url';
  $('extractBtn').textContent=mode==='url'?'▦ 读取链接，直接抽帧':mode==='upload'?'▦ 导入并抽帧':'▦ 直接抽帧';
  if(mode==='url')$('extractAudio').checked=false;
  if(mode==='saved'&&savedSource){localVideo.hidden=false;localVideo.src=`/api/jobs/${savedSource.id}/files/${savedSource.file.split('/').map(encodeURIComponent).join('/')}`;}
}
$('extractMode').addEventListener('change',updateExtractMode);
$('localVideoPath').addEventListener('input',()=>{localRegion=null;localFrame=null;localVideo.pause();localVideo.hidden=true;$('localCropWrap').hidden=true;});
$('streamCropEnabled').addEventListener('change',()=>{$('streamCropFields').hidden=!$('streamCropEnabled').checked;});
async function previewLocalPath() {
  const result=await api('/api/local-video/preview',{method:'POST',body:{path:$('localVideoPath').value}});
  if(localObjectUrl){URL.revokeObjectURL(localObjectUrl);localObjectUrl=null;}
  localRegion=null;localFrame=null;localVideo.src=result.url;localVideo.hidden=false;
  $('extractSource').textContent='已直接加载原视频，可拖动画面框选区域。不会在素材目录保存视频副本。';
}
bind('previewLocalPath',()=>busy($('previewLocalPath'),()=>typeof previewSelected21==='function'?previewSelected21():previewLocalPath()));
bind('pickVideo',()=>busy($('pickVideo'),async()=>{const result=await api('/api/pick-video',{method:'POST'});if(result.path){$('localVideoPath').value=result.path;await previewLocalPath();}}));
bind('streamOpenFirst',()=>openLogin($('streamUrls').value.split('\n').find(x=>x.trim())||'',$('streamOpenFirst')));
function drawLocal() {
  if(!localFrame) return;
  localCtx.clearRect(0,0,localCanvas.width,localCanvas.height);localCtx.drawImage(localFrame,0,0);drawSelection(localCtx,localRegion,localCanvas.width,localCanvas.height);
}
function grabLocal() {
  if(!localVideo.videoWidth||$('extractMode').value==='url') return;
  localCanvas.width=localVideo.videoWidth;localCanvas.height=localVideo.videoHeight;
  localFrame=document.createElement('canvas');localFrame.width=localCanvas.width;localFrame.height=localCanvas.height;
  localFrame.getContext('2d').drawImage(localVideo,0,0);drawLocal();$('localCropWrap').hidden=false;
}
makeCrop(localCanvas,()=>localRegion,c=>localRegion=c,drawLocal);
localVideo.addEventListener('loadeddata',grabLocal);localVideo.addEventListener('seeked',grabLocal);
$('videoFile').addEventListener('change',()=>{
  const file=$('videoFile').files[0];if(!file) return;
  if(localObjectUrl) URL.revokeObjectURL(localObjectUrl);
  savedSource=null;localRegion=null;localFrame=null;localObjectUrl=URL.createObjectURL(file);localVideo.src=localObjectUrl;localVideo.hidden=false;
  $('fileLabel').textContent=file.name;$('extractSource').textContent='已选择本地文件，点击开始后导入并抽帧。';
});
bind('resetLocalCrop',()=>{localRegion=null;drawLocal();});
bind('extractBtn',()=>busy($('extractBtn'),async()=>{
  if($('extractMode').value==='local' && typeof submitLocalBatch==='function'){await submitLocalBatch('rules');return;}
  if($('extractMode').value==='url' && typeof submitRuleLinks25==='function'){await submitRuleLinks25();return;}
  const opts=frameSettings();
  const mode=$('extractMode').value;
  if(mode==='url'){
    const urls=$('streamUrls').value.split('\n').map(s=>s.trim()).filter(Boolean);
    await api('/api/download',{method:'POST',body:{urls,output:$('output').value,login:$('streamLogin').checked,cookie_id:$('streamCookie')?.value||'',frames_only:true,max_height:Number($('streamHeight').value),frames:opts}});
  }
  else if(mode==='local')await api('/api/extract-local',{method:'POST',body:{path:$('localVideoPath').value,output:$('output').value,frames:opts}});
  else if (mode==='saved'&&savedSource) await api(`/api/jobs/${savedSource.id}/extract`,{method:'POST',body:{file:savedSource.file,output:$('output').value,frames:opts}});
  else {
    const file=$('videoFile').files[0];if(!file) throw new Error('请先选择一个视频文件。');
    const data=new FormData();data.append('file',file);data.append('options',JSON.stringify(opts));data.append('output',$('output').value);
    await api('/api/extract',{method:'POST',body:data,raw:true});
  }
  await refresh();goto('library');toast('抽帧任务已加入队列');
}));
function useSavedVideo(jid,name) {
  savedSource={id:jid,file:name};localRegion=null;localFrame=null;
  $('extractMode').querySelector('[value="saved"]').disabled=false;$('extractMode').value='saved';updateExtractMode();$('savedVideoName').textContent=name;
  if(localObjectUrl) {URL.revokeObjectURL(localObjectUrl);localObjectUrl=null;}
  localVideo.src=`/api/jobs/${jid}/files/${name.split('/').map(encodeURIComponent).join('/')}`;localVideo.hidden=false;
  $('videoFile').value='';$('fileLabel').textContent=name;$('extractSource').textContent='将直接处理已保存的视频，无需重复导入。';
  $('filesDialog').close();goto('extract');
}
function renderJobs() {
  $('totalJobs').textContent=jobs.length;$('doneJobs').textContent=jobs.filter(j=>j.status==='done').length;
  $('totalFrames').textContent=jobs.reduce((n,j)=>n+(j.frames||0),0);
  const filter=$('jobFilter').value;
  const visible=jobs.filter(j=>($('showCleaned')?.checked||(!j.materials_deleted&&!j.superseded_by))&&(filter==='all'||(filter==='active'?activeStatus(j.status):filter==='error'?['error','interrupted','cancelled'].includes(j.status):j.status===filter)));
  if(!visible.length) {$('jobs').innerHTML='<div class="jobs-empty"><strong>这里，准备装下你的灵感。</strong>从网页采集、链接下载或导入视频开始，素材会出现在这里。</div>';return;}
  $('jobs').innerHTML=visible.map(j=>`<article class="job"><div class="job-icon">${{download:'↓',capture:'◉',extract:'▦',crawler:'◈',dedup:'▧',stream:'⇣'}[j.kind]||'▤'}</div><div><div class="job-title">${html(j.title)}</div><p class="job-message">${html(j.message)}</p>${jobProgress(j)}<div class="job-meta">${new Date(j.created*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${j.frames||0} 张图片<br>${html(j.directory)}</div></div><div class="job-actions"><span class="status ${html(j.status)}">${statusLabels[j.status]||html(j.status)}</span><button class="text-btn" data-action="folder" data-id="${j.id}">打开目录</button><button class="text-btn" data-action="files" data-id="${j.id}">查看素材</button>${j.kind==='dedup'?`<button class="text-btn" data-action="dedup" data-id="${j.id}">查看去重结果</button>`:!activeStatus(j.status)&&j.frames?`<button class="text-btn" data-action="dedup-source" data-id="${j.id}">图片去重</button>`:''}${['waiting','waiting_source'].includes(j.status)?`<button class="subtle" data-action="resume" data-id="${j.id}">重新接管 / 继续</button>`:''}${activeStatus(j.status)&&j.status!=='recording'?`<button class="text-btn" data-action="cancel" data-id="${j.id}">取消</button>`:''}</div></article>`).join('');
}
const isVideo=name=>/\.(mp4|webm|mkv|mov|avi|flv|m4v|ts)$/i.test(name);
async function showFiles(jid) {
  await refresh();const j=jobs.find(j=>j.id===jid);if(!j) return;
  $('filesTitle').textContent=j.title;
  const files=j.files||[];
  $('filesList').innerHTML=files.length?files.slice(0,350).map(f=>{
    const href=`/api/jobs/${jid}/files/${f.name.split('/').map(encodeURIComponent).join('/')}`;
    return `<div class="file-row">${/\.(jpg|png)$/i.test(f.name)?`<img loading="lazy" src="${href}" alt="素材预览">`:''}<div><a href="${href}" target="_blank" rel="noopener">${html(f.name)}</a><small>${(f.size/1024/1024).toFixed(2)} MB</small></div>${isVideo(f.name)?`<button class="subtle" data-extract-id="${jid}" data-file="${html(f.name)}">用此视频抽帧</button>`:''}<a download href="${href}">保存</a></div>`;
  }).join('')+(files.length>350?'<p class="mini-note">仅显示前 350 个文件，其余请打开本机素材目录。</p>':''):'<p class="mini-note">暂时没有可展示文件。任务完成或取消后将更新，也可直接打开本机目录。</p>';
  if(!$('filesDialog').open) $('filesDialog').showModal();
}
$('jobs').addEventListener('click',event=>{
  const b=event.target.closest('button[data-action]');if(!b) return;
  const handlers={dedup:()=>openDedup(b.dataset.id),'dedup-source':async()=>{await refresh();$('dedupSourceJob').value=b.dataset.id;$('dedupSource').disabled=true;goto('dedup');},folder:()=>api(`/api/jobs/${b.dataset.id}/open-folder`,{method:'POST'}),files:()=>showFiles(b.dataset.id),resume:async()=>{await api(`/api/jobs/${b.dataset.id}/resume`,{method:'POST'});toast('已通知引擎继续，请等待结果');},cancel:async()=>{await api(`/api/jobs/${b.dataset.id}/cancel`,{method:'POST'});toast('已请求取消');await refresh();}};
  handlers[b.dataset.action]().catch(e=>toast(e.message,true));
});
$('filesList').addEventListener('click',event=>{const b=event.target.closest('[data-extract-id]');if(b) useSavedVideo(b.dataset.extractId,b.dataset.file);});
bind('closeFiles',()=>$('filesDialog').close());bind('refreshJobs',refresh);$('jobFilter').addEventListener('change',()=>renderJobs());

if($('crawlerBtn')) bind('crawlerBtn',()=>busy($('crawlerBtn'),async()=>{
  const result=await api('/api/crawler',{method:'POST',body:{platform:$('crawlerPlatform').value,mode:$('crawlerMode').value,
    query:$('crawlerQuery').value,max_items:Number($('crawlerLimit').value),comments:$('crawlerComments').checked,
    extract:$('crawlerExtract').checked,output:$('output').value,use_login:true,
    cookie_id:$('crawlerCookie')?.value||'',
    frames:{mode:$('crawlerFrameMode').value,value:Number($('crawlerFrameValue').value)}}});
  toast('平台采集已加入队列，请在弹出的浏览器中手动登录或完成验证。');await refresh();goto('library');
}));
if($('crawlerMode')) $('crawlerMode').addEventListener('change',()=>{
  const mode=$('crawlerMode').value;
  $('crawlerQuery').placeholder=mode==='search'?'输入关键词，多个关键词用英文逗号分隔':mode==='detail'?'每行一个视频网址或视频 ID':'每行一个作者主页网址或作者 ID';
});
refresh().catch(e=>toast(e.message,true));
let statePollPending=false,stateNextCheck=0,stateBackoff=2500;
setInterval(async()=>{if(statePollPending||Date.now()<stateNextCheck)return;statePollPending=true;try{await refresh();stateBackoff=2500;}catch(e){$('connectionText').textContent=e.connection?'本机服务已断开':'本机服务响应异常';$('connectionBadge').classList.add('disconnected');stateBackoff=Math.min(30000,stateBackoff*2);}finally{statePollPending=false;stateNextCheck=Date.now()+stateBackoff;}},2500);
