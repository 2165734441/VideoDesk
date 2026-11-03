'use strict';
let analysisId='',analysisData=null,workflowRefreshing=false,lastEvidence='',analysisTab='board';
let analysisExportRequest=null;
const fileUrl=(id,name)=>`/api/jobs/${id}/files/${name.split('/').map(encodeURIComponent).join('/')}`;
const secondsLabel=n=>`${Math.floor(Number(n)/60)}:${(Number(n)%60).toFixed(2).padStart(5,'0')}`;
function numberValue(id){const n=Number($(id).value);if(!Number.isFinite(n))throw new Error('请输入有效数字');return n;}
function jsonValue(id){try{return JSON.parse($(id).value||'{}');}catch{throw new Error('高级设置中的 JSON 格式不正确');}}
function onWorkflowPage(page){
  if(page==='settings')loadAnalysisSettings().catch(e=>toast(e.message,true));
  if(page==='analysis'){refreshAnalysisSources();if(analysisId)refreshAnalysis().catch(e=>toast(e.message,true));}
}
function refreshAnalysisSources(){
  const select=$('analysisSourceJob'),value=select.value;
  const candidates=jobs.filter(j=>(j.files||[]).some(f=>isVideo(f.name)));
  const signature=candidates.map(j=>j.id).join(',');
  if(select.dataset.signature!==signature){select.innerHTML='<option value="">请选择包含视频的素材</option>'+candidates.map(j=>`<option value="${j.id}">${html(j.title)}</option>`).join('');select.value=value;select.dataset.signature=signature;}
}
function updateAnalysisSource(){
  const mode=$('analysisSource').value;
  $('analysisInputField').hidden=mode!=='url';if($('analysisLocalSources'))$('analysisLocalSources').hidden=mode!=='local';$('analysisJobField').hidden=mode!=='job';$('analysisOnline').hidden=mode!=='url';$('analysisPick').hidden=mode!=='local';
  $('analysisInputLabel').textContent=mode==='local'?'本机视频完整路径':'视频链接';
  $('analysisInput').placeholder=mode==='local'?'例如 D:\\视频\\讲座.mp4':'粘贴抖音、B站或 YouTube 视频链接';
}
$('analysisSource').addEventListener('change',updateAnalysisSource);
$('analysisMode').addEventListener('change',()=>{$('analysisModeNote').textContent=$('analysisMode').value==='direct'?'不写入视频。重新细看时需要再次读取原链接，仍消耗流量。':'下载受管理的分析副本，可在设置中清理。';});
$('analysisPreset').addEventListener('change',()=>{$('analysisStrategy').value=$('analysisPreset').value;});
$('analysisStrategy').addEventListener('change',()=>{if(['compact','brief','full'].includes($('analysisStrategy').value))$('analysisPreset').value=$('analysisStrategy').value;});
document.querySelectorAll('[name="analysisPurpose"]').forEach(r=>r.addEventListener('change',()=>{$('analysisStart').textContent=r.value==='board'?'▦ 生成分镜与台词':r.value==='rules'?'▦ 打开完整规则抽帧':'▤ 生成字幕长图';}));
bind('analysisPick',()=>busy($('analysisPick'),async()=>{const result=await api('/api/pick-video',{method:'POST'});if(result.path)$('analysisInput').value=result.path;}));
bind('analysisOpenLogin',()=>openLogin($('analysisInput').value.trim().split(/\r?\n/)[0]||(typeof effectiveEpisodes==='function'?effectiveEpisodes()[0]?.input:''),$('analysisOpenLogin')));
function routeRules(start=null,duration=null){
  const source=$('analysisSource').value;
  if(source==='url'){$('extractMode').value='url';updateExtractMode();$('streamUrls').value=$('analysisInput').value;$('streamLogin').checked=$('analysisLogin').checked;}
  else if(source==='local'){$('extractMode').value='local';updateExtractMode();$('localVideoPath').value=$('analysisInput').value;}
  else {const j=jobs.find(j=>j.id===$('analysisSourceJob').value),f=j?.files.find(f=>isVideo(f.name));if(!j||!f)throw new Error('请选择包含视频的素材');useSavedVideo(j.id,f.name);}
  $('frameStart').value=start??$('analysisStartTime').value;$('frameDuration').value=duration??$('analysisDuration').value;goto('extract');
}
bind('analysisStart',()=>busy($('analysisStart'),async()=>{
  const kind=document.querySelector('[name="analysisPurpose"]:checked').value;
  if(kind==='rules'){routeRules();return;}
  const source=$('analysisSource').value;
  const sourceJob=source==='job'?$('analysisSourceJob').value:'';
  const input=sourceJob?(jobs.find(j=>j.id===sourceJob)?.source||'已有素材'):$('analysisInput').value.trim();
  const queuedLinks=source==='url'&&typeof effectiveEpisodes==='function'&&effectiveEpisodes().length>0;
  if((!input&&source!=='local'&&!queuedLinks)||source==='job'&&!sourceJob)throw new Error('请先选择视频来源');
  const body={input,source_job:sourceJob,output:$('output').value,mode:$('analysisMode').value,login:$('analysisLogin').checked,cookie_id:$('analysisCookies').value,
    strategy:$('analysisStrategy').value,pick:$('analysisPickMode').value,start:numberValue('analysisStartTime'),duration:numberValue('analysisDuration'),
    max_frames:numberValue('analysisMaxFrames'),max_sheets:numberValue('analysisMaxSheets'),max_cells_per_sheet:numberValue('analysisCellsPerSheet'),batch_seconds:numberValue('analysisBatch'),max_height:numberValue('analysisHeight'),
    transcript:$('analysisTranscript').value,transcript_file:$('analysisTranscriptFile').value.trim(),detail:$('analysisDetail').value,ocr:$('analysisOCR').checked,
    keep_frames:workflowSettings.board.outputs.frames,audio:workflowSettings.board.outputs.audio,performance:workflowSettings.board.performance,hotwords:$('analysisHotwords').value,terms:jsonValue('analysisTerms'),
    integral:$('analysisIntegral').checked,integral_seconds:numberValue('analysisIntegralSeconds'),scan_gap:numberValue('analysisScanGap'),tier_map:jsonValue('analysisTierMap'),kind,
    band:$('analysisBandMode').value==='manual'?[numberValue('analysisBandTop')/100,numberValue('analysisBandBottom')/100]:null,
    strip_step:numberValue('analysisStripStep'),strip_count:numberValue('analysisStripCount'),ai_package:typeof exportSettings!=='undefined'?exportSettings.ai_package:{enabled:true}};
  if(source==='url'&&typeof submitOnlineEpisodes==='function'){await submitOnlineEpisodes(kind,body);return;}
  if(source==='local'&&typeof submitLocalBatch==='function'){await submitLocalBatch(kind,body);return;}
  $('analysisImmediate').textContent='正在提交分析任务…';
  const job=await api('/api/analysis',{method:'POST',body});
  $('analysisImmediate').textContent='任务已加入队列，结果与进度显示在下方。';await refresh();await openAnalysis(job.id);toast('分析任务已创建');
}));
async function openAnalysis(id){analysisId=id;lastEvidence='';$('analysisResult').hidden=false;goto('analysis');await refreshAnalysis();$('analysisResult').scrollIntoView({behavior:'smooth',block:'start'});}
async function refreshAnalysis(){
  if(!analysisId||workflowRefreshing)return;workflowRefreshing=true;
  try{
    const data=await api(`/api/analysis/${analysisId}`);analysisData=data;
    const j=data.job;$('analysisResultTitle').textContent=j.title;
    $('analysisJobStatus').innerHTML=`<span class="status ${html(j.status)}">${html(statusLabels[j.status]||j.status)}</span> <span>${html(j.message)}</span>${jobProgress(j)}${activeStatus(j.status)?`<div class="button-row"><button class="text-btn" data-analysis-cancel>取消任务</button>${j.status==='waiting'?'<button class="subtle" data-analysis-resume>已完成验证，继续</button>':''}</div>`:''}`;
    $('analysisExport').disabled=j.status!=='done';$('analysisManualZoom').disabled=j.status!=='done';$('analysisRetry').hidden=activeStatus(j.status);
    $('analysisExportVideo').disabled=!j.options.available_video;
    if(!j.options.available_video)$('analysisExportVideo').checked=false;
    const signature=JSON.stringify(data.evidence);
    if(data.evidence&&signature!==lastEvidence){lastEvidence=signature;renderAnalysisEvidence();}
    if(j.status==='done'&&j.options.kind==='subtitles'){
      $('analysisSheets').innerHTML=`<a href="${fileUrl(j.id,'subtitle-strip.jpg')}" target="_blank"><img class="subtitle-result-image" src="${fileUrl(j.id,'subtitle-strip.jpg')}" alt="带时间索引的字幕长图"></a><p class="mini-note">字幕长图的每条时间见字幕索引文件；采样可能遗漏间隔内字幕。</p>`;
    }
  }finally{workflowRefreshing=false;}
}
function renderAnalysisEvidence(){
  const {job,evidence,transcript,transcript_info:info}=analysisData;
  $('analysisSheets').innerHTML=(evidence.sheets||[]).map(s=>`<figure class="storyboard-sheet"><a href="${fileUrl(job.id,s.file)}" target="_blank"><img loading="lazy" src="${fileUrl(job.id,s.file)}" alt="第 ${s.part} 段分镜图"></a><figcaption>第 ${s.part} 段 · ${s.width} × ${s.height} · ${s.cells.length} 格 · 点击查看原图</figcaption></figure>`).join('');
  $('analysisCells').innerHTML=(evidence.cells||[]).map(c=>`<article class="analysis-cell">${c.frame?`<a href="${fileUrl(job.id,c.frame)}" target="_blank"><img loading="lazy" src="${fileUrl(job.id,c.frame)}" alt="分镜 ${c.cell} 的高清画面"></a>`:''}<div><strong>第 ${c.part} 段 / 格 ${c.local_cell}</strong><small>${secondsLabel(c.from)}—${secondsLabel(c.to)}</small><p>${html(c.text||'此格没有对齐台词')}</p><button class="subtle" data-zoom-cell="${c.cell}">细看这段 ↗</button></div></article>`).join('');
  renderTranscript(transcript,info);showAnalysisTab(analysisTab);
}
function renderTranscript(rows,info={}){
  const note=(info.warning||(!rows.length?'没有可用台词。可导入字幕，或在设置中安装语音模型后重新处理。':'台词可能来自字幕或语音识别；请核对专有名词与时间。'))+(info.timing_method?' 语音时间是近似的句子范围，不是逐字时间。':'');
  $('analysisTranscriptRows').innerHTML=`<p class="notice">${html(note)}</p>`+rows.map(s=>`<article class="transcript-row"><button class="text-btn" data-zoom-start="${s.from}" data-zoom-end="${s.to}">${secondsLabel(s.from)}—${secondsLabel(s.to)}</button><p>${html(s.text)}</p></article>`).join('');
}
function showAnalysisTab(tab){analysisTab=tab;$('analysisSheets').hidden=tab!=='board';$('analysisCells').hidden=tab!=='board';$('analysisTranscriptPanel').hidden=tab!=='transcript';$('analysisTabBoard').classList.toggle('selected',tab==='board');$('analysisTabTranscript').classList.toggle('selected',tab==='transcript');}
bind('analysisTabBoard',()=>showAnalysisTab('board'));bind('analysisTabTranscript',()=>showAnalysisTab('transcript'));
bind('analysisResultFolder',()=>api(`/api/jobs/${analysisId}/open-folder`,{method:'POST'}));
bind('analysisFiles',()=>showFiles(analysisId));
bind('analysisRetry',()=>busy($('analysisRetry'),async()=>{const j=await api(`/api/analysis/${analysisId}/retry`,{method:'POST'});await refresh();await openAnalysis(j.id);}));
async function zoomAnalysis(body){const j=await api(`/api/analysis/${analysisId}/zoom`,{method:'POST',body});await refresh();await openAnalysis(j.id);toast('片段细看任务已创建');}
bind('analysisManualZoom',()=>busy($('analysisManualZoom'),()=>zoomAnalysis({start:numberValue('analysisZoomStart'),duration:numberValue('analysisZoomDuration')})));
bind('analysisLocate',()=>busy($('analysisLocate'),async()=>{if(!$('analysisSearch').value.trim()){renderTranscript(analysisData.transcript,analysisData.transcript_info);return;}const result=await api(`/api/analysis/${analysisId}/locate`,{method:'POST',body:{query:$('analysisSearch').value}});renderTranscript(result.matches,{warning:result.matches.length?`找到 ${result.matches.length} 段匹配台词。点击时间可细看。`:'没有找到匹配，试试原句中的较短关键词。'});}));
bind('analysisRules',()=>{if(!analysisData)return;const opts=analysisData.job.options;$('analysisSource').value=opts.source_job?'job':opts.input.startsWith('http')?'url':'local';$('analysisInput').value=opts.input;$('analysisSourceJob').value=opts.source_job||'';updateAnalysisSource();routeRules(numberValue('analysisZoomStart'),numberValue('analysisZoomDuration'));});
bind('analysisExport',()=>busy($('analysisExport'),async()=>{const body={output:$('output').value,frames:$('analysisExportFrames').checked,audio:$('analysisExportAudio').checked,video:$('analysisExportVideo').checked};const signature=JSON.stringify({id:analysisId,...body});if(analysisExportRequest?.signature!==signature)analysisExportRequest={signature,id:crypto.randomUUID()};await api(`/api/analysis/${analysisId}/export`,{method:'POST',body:{...body,request_id:analysisExportRequest.id}});await refresh();goto('library');toast('正在打包 AI 分析包，完成后可查看文件或打开目录');}));
$('analysisResult').addEventListener('click',async event=>{const b=event.target.closest('button');if(!b||b.disabled)return;try{if(b.dataset.zoomCell)await busy(b,()=>zoomAnalysis({cell:Number(b.dataset.zoomCell)}));else if(b.dataset.zoomStart){$('analysisZoomStart').value=b.dataset.zoomStart;$('analysisZoomDuration').value=Math.max(.1,Number(b.dataset.zoomEnd)-Number(b.dataset.zoomStart));toast('已选择这段台词，可点击“生成片段细看”');}else if(b.hasAttribute('data-analysis-cancel')){await api(`/api/jobs/${analysisId}/cancel`,{method:'POST'});await refreshAnalysis();}else if(b.hasAttribute('data-analysis-resume')){await api(`/api/analysis/${analysisId}/resume`,{method:'POST'});toast('已通知任务重试');}}catch(e){toast(e.message,true);}});

async function loadAnalysisSettings(){
  const data=await api('/api/analysis-settings');
  $('analysisEngineStatus').textContent=data.engine.ready?'分镜引擎已就绪':'分镜组件待安装';
  const model=data.components.models.find(x=>x.id==='sensevoice');
  const note=!data.components.asr_runtime?'本地语音组件暂不可用，请在下方运行组件中检测并修复；仍可使用现成字幕。':model?.ready?'本地语音模型已安装，可离线转写。':`本地语音模型未安装${model?`，下载约 ${(model.bytes/1048576).toFixed(0)} MB`:''}；仍可使用现成字幕与纯图分析。`;
  $('analysisModelNote').textContent=note;$('modelStatus').textContent=note+` OCR：${data.components.ocr?'已就绪':'组件或模型未就绪，请查看运行组件状态'}。`;
  $('modelInstall').textContent=model?.ready?'重新安装语音模型':'安装本地语音模型';
  $('asrBackend').value=data.settings.asr;$('asrBase').value=data.settings.base_url;$('asrModel').value=data.settings.model;$('asrKey').placeholder=data.settings.key_present?'已保存密钥，留空保留':'未配置密钥';
  const options='<option value="">使用浏览器登录态</option>'+data.cookies.map(c=>`<option value="${c.id}">${html(c.name)} · ${html((c.domains||[]).slice(0,2).join(', '))}</option>`).join('');
  ['analysisCookies','downloadCookie','streamCookie','crawlerCookie'].forEach(id=>{const e=$(id);if(e){const v=e.value;e.innerHTML=options;e.value=v;}});
  $('cookieList').textContent=data.cookies.length?data.cookies.map(c=>`${c.name}：${(c.domains||[]).join(', ')}`).join('\n'):'尚未导入 Cookie 文件';
  const cache=await api('/api/media-cache');$('cacheStatus').textContent=`分析缓存 ${(cache.bytes/1048576).toFixed(1)} MB · ${cache.entries.length} 个视频。位置：${cache.path}。清理仅处理应用的分析副本，原视频和正式素材保留。`;
}
bind('cookieImport',()=>busy($('cookieImport'),async()=>{const f=$('cookieFile').files[0];if(!f)throw new Error('请选择 Cookie 文件');await api('/api/cookies/import',{method:'POST',body:{name:f.name,text:await f.text()}});$('cookieFile').value='';await loadAnalysisSettings();toast('Cookie 已检查并导入，仅保存在本机');}));
async function installModel(path){await api('/api/models/install',{method:'POST',body:{id:'sensevoice',path}});await refresh();goto('library');toast('模型安装任务已创建，任务列表会显示下载和校验进度');}
bind('modelInstall',()=>busy($('modelInstall'),()=>installModel(null)));bind('modelImport',()=>busy($('modelImport'),()=>{if(!$('modelOfflinePath').value.trim())throw new Error('请填写离线模型包的完整路径');return installModel($('modelOfflinePath').value.trim());}));
bind('modelExport',()=>busy($('modelExport'),async()=>{await api('/api/models/export',{method:'POST',body:{id:'sensevoice',output:$('output').value}});await refresh();goto('library');toast('正在准备官方离线模型包');}));
bind('asrSave',()=>busy($('asrSave'),async()=>{const body={asr:$('asrBackend').value,base_url:$('asrBase').value.trim(),model:$('asrModel').value.trim()};if($('asrKey').value)body.api_key=$('asrKey').value;await api('/api/analysis-settings',{method:'POST',body});$('asrKey').value='';await loadAnalysisSettings();toast('转写设置已保存');}));
bind('cacheClear',()=>busy($('cacheClear'),async()=>{await api('/api/media-cache/clear',{method:'POST'});await loadAnalysisSettings();toast('分析缓存已清理');}));
bind('shutdownApp',async()=>{const state=await api('/api/state');const active=state.jobs.filter(j=>activeStatus(j.status));if(active.length){toast('请先停止并保存录制、等待任务完成或取消任务，再退出后台。',true);return;}await api('/api/shutdown',{method:'POST'});toast('后台服务正在退出，可关闭此网页。');});
for(const [id,parent] of [['downloadCookie','downloadUrls'],['streamCookie','streamUrls'],['crawlerCookie','crawlerQuery']]){
  const label=document.createElement('label');label.className='field';label.innerHTML=`Cookie 文件（可选，优先于浏览器账号）<select id="${id}"><option value="">使用浏览器登录态</option></select>`;$(parent).closest('label').after(label);
}
// One task source of truth: the library opens the exact same result view.
const originalRenderJobs=renderJobs;
renderJobs=function(){originalRenderJobs();document.querySelectorAll('#jobs .job').forEach(article=>{const id=article.querySelector('[data-id]')?.dataset.id,j=jobs.find(j=>j.id===id);if(!j)return;const actions=article.querySelector('.job-actions');if(j.kind==='analysis'){const b=document.createElement('button');b.className='subtle';b.textContent='查看分镜与台词';b.addEventListener('click',()=>openAnalysis(id).catch(e=>toast(e.message,true)));actions.prepend(b);}else if(!activeStatus(j.status)&&(j.files||[]).some(f=>isVideo(f.name))){const b=document.createElement('button');b.className='subtle';b.textContent='生成 AI 分镜';b.addEventListener('click',()=>{refreshAnalysisSources();$('analysisSource').value='job';$('analysisSourceJob').value=id;updateAnalysisSource();goto('analysis');});actions.prepend(b);}});};
// Keep contextual exits available without expanding the main navigation.
for(const page of ['capture','download','crawler','extract','dedup']){const e=$('page-'+page),bar=document.createElement('div');bar.className='context-back';bar.innerHTML='<button class="text-btn">← 返回工作台</button>';bar.querySelector('button').onclick=()=>goto(page==='extract'?'analysis':page==='dedup'?'library':'import');e.prepend(bar);}
const dedupShortcut=document.createElement('button');dedupShortcut.className='subtle';dedupShortcut.textContent='照片与截图去重';dedupShortcut.onclick=()=>goto('dedup');$('refreshJobs').before(dedupShortcut);
setInterval(()=>{refreshAnalysisSources();if(currentPage==='analysis'&&analysisId)refreshAnalysis().catch(()=>{});},2500);
loadAnalysisSettings().catch(e=>toast('分析组件状态读取失败：'+e.message,true));
goto('import');
