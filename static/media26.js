'use strict';
// Live capture and translation use the shared local session and task events.
(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  const content = document.querySelector('main .content');
  for (const [page, label] of [['live','◉ 直播录播与回放'], ['translate','文 英文视频转中文']]) {
    const button = document.createElement('button'); button.className='nav'; button.dataset.page=page; button.textContent=label;
    button.addEventListener('click',()=>{goto(page);load26().catch(e=>toast(e.message,true));}); nav.insertBefore(button,nav.lastElementChild);
  }
  const live = document.createElement('section'); live.id='page-live';live.className='page';live.hidden=true;
  live.innerHTML=`<div class="page-heading"><div><div class="eyebrow">LIVE TO REPLAY</div><h1>等他开播，替你留住。</h1><p>同时录制多个抖音直播间，按主播整理回放。关闭网页后，托盘服务继续录制。</p></div><span class="badge">默认四路 · 直接保存直播流</span></div>
  <div class="two-columns"><section class="panel padded"><h2 id="liveEditTitle">添加主播</h2>
  <input type="hidden" id="liveEditId"><label class="field">主播昵称（可选）<input id="liveName" maxlength="100"></label>
  <label class="field">直播间、主页或分享链接<textarea id="liveUrls" rows="3" placeholder="每行一个抖音链接；多位主播可一起添加"></textarea></label>
  <div class="inline-fields"><label class="field">录播画质<select id="liveQuality"><option value="origin">原画优先</option><option value="high">高清优先</option><option value="standard">标清优先</option></select></label><label class="field">并发录播上限<input id="liveConcurrency" type="number" min="1" max="8" value="4"></label></div>
  <label class="checkbox-line"><input id="liveAuto" type="checkbox">开播后自动录制</label>
  <label class="checkbox-line"><input id="liveTimed" type="checkbox">仅在指定时间段监控和录制（北京时间）</label>
  <div id="liveWindow" hidden><div class="weekdays26">${['一','二','三','四','五','六','日'].map((d,i)=>`<label><input type="checkbox" data-live-day="${i}" checked>周${d}</label>`).join('')}</div><div class="inline-fields"><label class="field">开始<input id="liveFrom" type="time" value="00:00"></label><label class="field">结束<input id="liveTo" type="time" value="00:00"></label></div><p class="mini-note">相同时间表示全天；结束早于开始表示跨午夜。定时任务需要托盘服务运行，电脑休眠时无法录制。</p></div>
  <div class="button-row"><button id="liveSave" class="primary">保存主播</button><button id="liveSaveStart" class="subtle">保存并立即录制</button><button id="liveReset" class="text-btn">清空 / 取消编辑</button></div><p class="mini-note">保存位置沿用顶部设置。录播分段保留，不会自动删除旧回放。停止按钮会关闭此主播的自动监控。</p></section>
  <section class="panel padded"><h2>导入抖音关注列表</h2><p class="muted">先在映格自己的浏览器登录抖音，再读取网页实际加载的关注用户。</p><div class="button-row"><button id="liveLogin" class="subtle">打开抖音，手动登录</button><button id="liveFollowing" class="subtle">读取关注列表</button></div><p id="liveImportStatus" role="status" class="mini-note">导入后，勾选需要录播的主播，再添加到列表。</p><div id="liveFollowingRows" class="following26"></div><button id="liveImportSelected" class="primary" hidden>添加所选主播</button><hr><h2>设备与录播状态</h2><div id="hardware26" class="notice">正在检查硬件…</div><p class="mini-note">直接保存直播流通常不需要显卡。生成兼容回放和压制字幕时才需要编码。磁盘剩余不足 2 GB 时安全停止写入。</p></section></div>
  <section class="panel section-space"><div class="panel-head"><h2>主播监控</h2><button id="liveReload" class="text-btn">刷新</button></div><div id="liveAnchors" class="padded"></div></section>
  <section class="panel section-space"><div class="panel-head"><h2>回放</h2><select id="liveReplaySelect"><option value="">选择一场录播</option></select></div><div class="padded"><div id="liveReplaySegments" class="button-row"></div><video id="liveReplayVideo" controls playsinline preload="metadata" class="replay26" hidden></video><div class="button-row"><label>倍速 <select id="liveReplayRate"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label><button id="liveReplayTranslate" class="subtle" hidden>将这一段翻译为中文</button><button id="liveCompatible" class="text-btn" hidden>生成兼容回放</button></div><p id="liveReplayNote" role="status" class="mini-note">完成的录播片段会显示在这里。</p></div></section>`;
  const translate = document.createElement('section');translate.id='page-translate';translate.className='page';translate.hidden=true;
  translate.innerHTML=`<div class="page-heading"><div><div class="eyebrow">WATCH IN YOUR LANGUAGE</div><h1>英文内容，用中文看懂。</h1><p>英文在上、中文在下，统一中文声音配音。原视频保留，翻译结果另存。</p></div><span class="badge">本地优先 · 显卡 / CPU 自动选择</span></div>
  <div class="two-columns"><section class="panel padded"><h2>1. 选择英文视频</h2><label class="field">本机视频完整路径<textarea id="translatePaths" rows="3" placeholder="每行一个本机视频路径"></textarea></label><button id="translatePick" class="subtle">选择本机视频</button><label class="field section-space">或选择一段已有回放<select id="translateReplay"><option value="">使用上面的本机视频</option></select></label><label class="field">英文字幕文件（可选）<input id="translateSubtitle" placeholder="完整 .srt / .vtt / .json 路径；留空则语音识别"></label>
  <div class="inline-fields"><label class="field">处理方式<select id="translateBackend"><option value="local">本机离线模型</option><option value="cloud">使用我配置的云服务</option></select></label><label class="field">设备<select id="translateDevice"><option value="auto">自动选择</option><option value="gpu">优先显卡</option><option value="cpu">仅 CPU</option></select></label></div>
  <label class="checkbox-line"><input type="checkbox" id="translateBackground" checked>分离英文人声，尽量保留背景音乐和环境声</label><label class="checkbox-line"><input type="checkbox" id="translateReview" checked>先检查、修改双语字幕，再生成配音</label><div class="inline-fields"><label class="field">字幕字号<input type="number" id="translateFont" value="26" min="12" max="96"></label><label class="field">距底部（720p 基准）<input type="number" id="translateMargin" value="36" min="0" max="500"></label></div><button id="translateStart" class="primary wide">开始生成中文视频</button><p class="mini-note">配音使用统一声音，不修改人物口型。原画面已有英文文字时会保留；可调整新字幕位置。关闭背景声分离可提速，输出仅中文配音。</p></section>
  <section class="panel padded"><h2>2. 本地组件与云服务</h2><div id="translateComponents" class="notice" role="status">检查翻译组件…</div><div class="button-row"><button id="translateInstallCpu" class="subtle">安装 CPU 组件与模型</button><button id="translateInstallGpu" class="subtle">安装 NVIDIA 加速组件</button></div><p class="mini-note">首次安装需联网下载独立 AI 环境和模型；NVIDIA 组件下载量较大。安装后视频可以留在本机处理。</p><details><summary>离线模型导入 / 导出</summary><div class="button-row"><button id="translateExportModels" class="text-btn">导出已下载模型</button></div><label class="field">离线模型 ZIP 完整路径<input id="translateOfflinePath"></label><button id="translateImportModels" class="subtle">校验并导入模型</button><p class="mini-note">目标电脑仍需先安装对应 AI 运行组件。</p></details><details class="section-space"><summary>可选云服务设置</summary><p class="mini-note">兼容音频转写、聊天翻译和语音合成接口。选择云模式后才发送内容；保留背景声仍在本地分离。密钥不进入任务、日志或导出包。</p>${[['asr','英文语音识别'],['translation','英中翻译'],['tts','中文配音']].map(([k,n])=>`<fieldset class="service26"><legend>${n}</legend><label class="field">API 基础地址<input id="service26-${k}-url" placeholder="https://服务地址/v1"></label><div class="inline-fields"><label class="field">模型<input id="service26-${k}-model"></label><label class="field">API 密钥<input type="password" autocomplete="new-password" id="service26-${k}-key" placeholder="留空保留原密钥"></label></div>${k==='tts'?'<label class="field">声音 ID<input id="service26-tts-voice" value="alloy"></label>':''}</fieldset>`).join('')}<button id="translateSaveServices" class="subtle">保存服务配置</button></details></section></div>
  <section class="panel section-space"><div class="panel-head"><h2>翻译任务</h2><select id="translateJobSelect"><option value="">选择任务</option></select></div><div class="padded"><div id="translateJobStatus" role="status" class="muted">选择视频开始翻译。</div><div class="button-row"><button id="translateLoadRows" class="subtle" hidden>查看 / 编辑双语字幕</button><button id="translateRetry" class="subtle" hidden>重试未完成任务</button><button id="translateCancel" class="text-btn" hidden>取消</button><button id="translateFolder" class="text-btn" hidden>打开目录</button></div><video id="translateVideo" controls playsinline preload="metadata" class="replay26" hidden></video><div id="translateEditor" hidden><p class="mini-note">修改中文后会重新生成配音；已有视频保留。超时语句需要缩短译文。</p><div id="translateRows"></div><div class="button-row"><button id="translatePrev" class="text-btn">上一页</button><span id="translatePageLabel"></span><button id="translateNext" class="text-btn">下一页</button><button id="translateContinue" class="primary">保存字幕并生成中文视频</button></div></div></div></section>`;
  content.insertBefore(live,content.querySelector('footer'));content.insertBefore(translate,content.querySelector('footer'));
  $('translateComponents').insertAdjacentHTML('beforebegin','<label class="field">AI 组件保存位置<div class="inline-inputs"><input id="translateComponentPath" placeholder="可选择空间充足的磁盘，也可使用已安装组件的目录"><button id="translateComponentSave" class="subtle">保存位置</button></div></label>');
  let liveState={anchors:[],settings:{}},replays=[],following=[],replayJob=null,replayIndex=0,rows=[],rowPage=0,editorId='',serviceLoaded=false,loading=false,lastLoaded=0;
  const post=(url,body={},timeoutMs)=>api(url,{method:'POST',body,timeoutMs});
  function selectedJob(){return jobs.find(j=>j.id===$('translateJobSelect').value);}
  function resetAnchor(){ $('liveEditId').value='';$('liveName').value='';$('liveUrls').value='';$('liveUrls').disabled=false;$('liveAuto').checked=false;$('liveTimed').checked=false;$('liveWindow').hidden=true;$('liveEditTitle').textContent='添加主播'; }
  function anchorOptions(url){return {id:$('liveEditId').value,name:$('liveName').value,url,enabled:$('liveAuto').checked,quality:$('liveQuality').value,windows:$('liveTimed').checked?[{days:[...document.querySelectorAll('[data-live-day]:checked')].map(e=>+e.dataset.liveDay),start:$('liveFrom').value,end:$('liveTo').value}]:[]};}
  async function saveAnchor(start){
    const urls=$('liveUrls').value.split(/\n/).map(v=>v.trim()).filter(Boolean);if(!urls.length)throw Error('请先填写主播链接');
    if($('liveEditId').value&&urls.length!==1)throw Error('编辑时请保留一个主播链接');
    await post('/api/live/settings',{...liveState.settings,output:$('output').value,concurrency:+$('liveConcurrency').value});
    for(const url of urls){const item=await post('/api/live/anchors',anchorOptions(url));if(start)await post(`/api/live/anchors/${item.id}/start`);}
    resetAnchor();await load26();toast(start?'已提交录播':'主播已保存');
  }
  bind('liveSave',()=>saveAnchor(false));bind('liveSaveStart',()=>saveAnchor(true));bind('liveReset',resetAnchor);
  $('liveTimed').onchange=()=>{$('liveWindow').hidden=!$('liveTimed').checked;};
  bind('liveLogin',()=>post('/api/browser/open',{url:'https://www.douyin.com/follow'},60000));
  bind('liveFollowing',async()=>{const result=await post('/api/live/following',{},150000);following=result.users;$('liveImportStatus').textContent=result.message+(result.complete?'（已加载完整列表）':'（未确认完整）');$('liveFollowingRows').innerHTML=following.map((u,i)=>`<label class="checkbox-line"><input type="checkbox" data-follow26="${i}">${html(u.name)}</label>`).join('');$('liveImportSelected').hidden=!following.length;});
  bind('liveImportSelected',async()=>{const selected=[...document.querySelectorAll('[data-follow26]:checked')];if(!selected.length)throw Error('请选择主播');for(const element of selected){const u=following[+element.dataset.follow26];try{await post('/api/live/anchors',{...u,enabled:false});}catch(e){if(e.status!==409)throw e;}}await load26();toast('已添加到主播列表，请开启需要的自动监控');});
  bind('liveReload',load26);
  $('liveAnchors').onclick=async event=>{
    const button=event.target.closest('[data-live-action]');if(!button)return;const item=liveState.anchors.find(x=>x.id===button.dataset.id);if(!item)return;
    try{
      if(button.dataset.liveAction==='edit'){
        $('liveEditId').value=item.id;$('liveName').value=item.name;$('liveUrls').value=item.url;$('liveQuality').value=item.quality;$('liveAuto').checked=item.enabled;
        const w=item.windows[0];$('liveTimed').checked=!!w;$('liveWindow').hidden=!w;if(w){$('liveFrom').value=w.start;$('liveTo').value=w.end;document.querySelectorAll('[data-live-day]').forEach(e=>e.checked=w.days.includes(+e.dataset.liveDay));}$('liveEditTitle').textContent='编辑主播';$('liveUrls').focus();return;
      }
      if(button.dataset.liveAction==='login')await post('/api/browser/open',{url:item.url},60000);
      if(button.dataset.liveAction==='start')await post(`/api/live/anchors/${item.id}/start`);
      if(button.dataset.liveAction==='stop')await post(`/api/live/anchors/${item.id}/stop`);
      if(button.dataset.liveAction==='toggle')await post('/api/live/anchors',{...item,enabled:!item.enabled});
      if(button.dataset.liveAction==='delete')await api(`/api/live/anchors/${item.id}`,{method:'DELETE'});
      await load26();
    }catch(e){toast(e.message,true);}
  };
  function renderAnchors(){ $('liveAnchors').innerHTML=liveState.anchors.length?liveState.anchors.map(u=>`<article class="anchor26"><div><strong>${html(u.name)}</strong><p class="muted">${html(u.runtime?.message||(u.enabled?'等待开播检查':'未开启自动监控'))}</p><small>${u.enabled?'自动监控已开启':'自动监控已关闭'}${u.windows.length?' · 有定时设置':''}</small></div><div class="button-row">${u.active_job?`<button class="subtle" data-live-action="stop" data-id="${u.id}">停止并保存</button>`:`<button class="primary" data-live-action="start" data-id="${u.id}">立即录制</button>`}<button class="text-btn" data-live-action="toggle" data-id="${u.id}">${u.enabled?'关闭监控':'开启监控'}</button><button class="text-btn" data-live-action="login" data-id="${u.id}">打开主播</button><button class="text-btn" data-live-action="edit" data-id="${u.id}">编辑</button>${!u.active_job?`<button class="text-btn" data-live-action="delete" data-id="${u.id}">移除</button>`:''}</div></article>`).join(''):'<p class="muted">添加主播后，可立即录制或开启自动监控。</p>'; }
  function fillSelect(select, options, placeholder){const current=select.value;const signature=JSON.stringify(options);if(select.dataset.signature===signature)return;select.dataset.signature=signature;select.innerHTML=`<option value="">${placeholder}</option>`+options.map(([v,n])=>`<option value="${html(v)}">${html(n)}</option>`).join('');if(options.some(o=>o[0]===current))select.value=current;}
  function renderReplayLists(){
    fillSelect($('liveReplaySelect'),replays.map(j=>[j.id,j.title+' · '+new Date(j.created*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})]),'选择一场录播');
    fillSelect($('translateReplay'),replays.flatMap(j=>(j.replay||[]).map((s,i)=>[j.id+'|'+s.name,j.title+' · 第 '+(i+1)+' 段'])),'使用上面的本机视频');
  }
  function showReplay(){
    replayJob=replays.find(j=>j.id===$('liveReplaySelect').value);const segments=replayJob?.replay||[];replayIndex=Math.min(replayIndex,Math.max(0,segments.length-1));
    $('liveReplaySegments').innerHTML=segments.map((s,i)=>`<button class="${i===replayIndex?'primary':'subtle'}" data-segment26="${i}">第 ${i+1} 段${s.gap_before?' · 曾中断':''}</button>`).join('');
    const segment=segments[replayIndex];$('liveReplayVideo').hidden=!segment;$('liveReplayTranslate').hidden=!segment;$('liveCompatible').hidden=!segment||activeStatus(replayJob.status);
    if(segment){const url=`/api/jobs/${replayJob.id}/files/${encodeURIComponent(segment.preview||segment.name)}`;if($('liveReplayVideo').dataset.source!==url){$('liveReplayVideo').src=url;$('liveReplayVideo').dataset.source=url;}$('liveReplayNote').textContent='可以拖动和倍速播放。若浏览器不支持原视频编码，请生成兼容回放，或打开目录使用本机播放器。';}
  }
  $('liveReplaySelect').onchange=()=>{replayIndex=0;showReplay();};$('liveReplaySegments').onclick=e=>{const b=e.target.closest('[data-segment26]');if(b){replayIndex=+b.dataset.segment26;showReplay();}};
  $('liveReplayRate').onchange=()=>{$('liveReplayVideo').playbackRate=+$('liveReplayRate').value;};
  bind('liveCompatible',async()=>{await post(`/api/live/replays/${replayJob.id}/preview/${replayIndex}`);toast('正在生成兼容回放');});
  bind('liveReplayTranslate',()=>{const s=replayJob.replay[replayIndex];$('translateReplay').value=replayJob.id+'|'+s.name;goto('translate');});
  bind('translatePick',async()=>{const result=await post('/api/pick-video');if(result.paths?.length||result.path){$('translatePaths').value=(result.paths||[result.path]).join('\n');$('translateReplay').value='';}});
  bind('translateStart',async()=>{
    const selected=$('translateReplay').value.split('|');const body={paths:$('translatePaths').value.split(/\n/).map(v=>v.trim()).filter(Boolean),output:$('output').value,subtitles:$('translateSubtitle').value.trim(),device:$('translateDevice').value,backend:$('translateBackend').value,background:$('translateBackground').checked,review:$('translateReview').checked,font_size:+$('translateFont').value,margin_bottom:+$('translateMargin').value};
    if(selected[0]){body.paths=[];body.source_job=selected[0];body.relative=selected[1];}
    const result=await post('/api/translation',body);await refresh();renderTranslationJobs();$('translateJobSelect').value=result.id;renderTranslationStatus();toast(`已提交 ${result.ids.length} 个翻译任务`);
  });
  for(const [id,gpu] of [['translateInstallCpu',false],['translateInstallGpu',true]])bind(id,async()=>{const result=await post('/api/translation/components/install',{gpu});toast('已开始安装，进度见素材库');await refresh();});
  bind('translateExportModels',async()=>{await post('/api/translation/models/export');toast('已提交导出，完成后可在素材库打开目录');});
  bind('translateImportModels',async()=>{await post('/api/translation/models/import',{path:$('translateOfflinePath').value.trim()});toast('已提交校验安装');});
  bind('translateSaveServices',async()=>{const data={};for(const role of ['asr','translation','tts'])data[role]={base_url:$(`service26-${role}-url`).value.trim(),model:$(`service26-${role}-model`).value.trim(),api_key:$(`service26-${role}-key`).value,voice:role==='tts'?$('service26-tts-voice').value:'alloy'};await post('/api/translation/services',data);document.querySelectorAll('.service26 input[type=password]').forEach(e=>e.value='');toast('云服务配置已保存');});
  bind('translateComponentSave',async()=>{await post('/api/translation/components/location',{path:$('translateComponentPath').value.trim()});await load26();toast('AI 组件位置已保存，已有组件会自动检测');});
  function renderTranslationJobs(){fillSelect($('translateJobSelect'),jobs.filter(j=>j.kind==='translation').map(j=>[j.id,j.title]),'选择任务');renderTranslationStatus();}
  function renderTranslationStatus(){
    const job=selectedJob();if(!job)return;
    $('translateJobStatus').innerHTML=`<strong>${html(job.message)}</strong>${job.status==='waiting'?'':jobProgress(job)}<p class="mini-note">${html(Object.entries(job.devices||{}).map(([k,v])=>k+': '+v).join(' · '))}${job.encoder?' · '+html(job.encoder):''}</p>`;
    $('translateLoadRows').hidden=false;$('translateFolder').hidden=false;$('translateCancel').hidden=!activeStatus(job.status);$('translateRetry').hidden=!['error','cancelled','interrupted'].includes(job.status);
    const video=$('translateVideo');video.hidden=!job.translated_video;if(job.translated_video){const url=`/api/jobs/${job.id}/files/${encodeURIComponent(job.translated_video)}`;if(video.dataset.source!==url){video.src=url;video.dataset.source=url;}}
    if(editorId!==job.id)$('translateEditor').hidden=true;
  }
  $('translateJobSelect').onchange=renderTranslationStatus;
  bind('translateCancel',()=>post(`/api/jobs/${selectedJob().id}/cancel`));bind('translateRetry',()=>post(`/api/jobs/${selectedJob().id}/resume`));bind('translateFolder',()=>post(`/api/jobs/${selectedJob().id}/open-folder`));
  function saveRows(){document.querySelectorAll('[data-row26]').forEach(e=>{rows[+e.dataset.row26][e.dataset.language]=e.value;});}
  function renderRows(){const start=rowPage*30;$('translateRows').innerHTML=rows.slice(start,start+30).map((r,i)=>`<div class="subtitle26"><small>${r.start.toFixed(2)} → ${r.end.toFixed(2)} 秒</small><label>英文<textarea rows="2" data-row26="${start+i}" data-language="en">${html(r.en)}</textarea></label><label>中文<textarea rows="2" data-row26="${start+i}" data-language="zh">${html(r.zh)}</textarea></label></div>`).join('');$('translatePageLabel').textContent=`${rowPage+1} / ${Math.max(1,Math.ceil(rows.length/30))}`;$('translatePrev').disabled=!rowPage;$('translateNext').disabled=start+30>=rows.length;}
  bind('translateLoadRows',async()=>{const job=selectedJob();const data=await api(`/api/translation/${job.id}/subtitles`);rows=data.segments;if(!rows.length)throw Error('字幕尚未生成，请稍后再看');editorId=job.id;rowPage=0;$('translateEditor').hidden=false;renderRows();});
  bind('translatePrev',()=>{saveRows();rowPage--;renderRows();});bind('translateNext',()=>{saveRows();rowPage++;renderRows();});
  bind('translateContinue',async()=>{saveRows();await post(`/api/translation/${editorId}/continue`,{segments:rows});toast('字幕已保存，正在生成中文配音');$('translateEditor').hidden=true;});
  async function load26(){
    if(loading)return;loading=true;
    try{const [state,replay,component]=await Promise.all([api('/api/live'),api('/api/live/replays'),api('/api/translation/components')]);liveState=state;replays=replay.jobs;renderAnchors();renderReplayLists();if(currentPage==='live'&&$('liveReplaySelect').value)showReplay();if(document.activeElement!==$('liveConcurrency'))$('liveConcurrency').value=state.settings.concurrency;
      $('translateComponents').textContent=`运行组件：${component.runtime.gpu?'NVIDIA 加速版已安装':component.runtime.cpu?'CPU 版已安装':'未安装'}。`+component.models.map(m=>m.name+'：'+(m.ready?'已就绪':'未安装')).join('；');
      if(document.activeElement!==$('translateComponentPath'))$('translateComponentPath').value=component.path;
      if(!serviceLoaded){const services=await api('/api/translation/services');for(const [k,v]of Object.entries(services)){ $(`service26-${k}-url`).value=v.base_url||'';$(`service26-${k}-model`).value=v.model||'';if(k==='tts')$('service26-tts-voice').value=v.voice||'alloy';if(v.has_key)$(`service26-${k}-key`).placeholder='已保存密钥，留空保留';}serviceLoaded=true;}
      renderTranslationJobs();lastLoaded=Date.now();
    }finally{loading=false;}
  }
  window.addEventListener('video-desk-state',()=>{renderTranslationJobs();if(['live','translate'].includes(currentPage)&&Date.now()-lastLoaded>8000)load26().catch(e=>toast(e.message,true));});
  api('/api/hardware',{timeoutMs:60000}).then(h=>{$('hardware26').textContent=(h.gpus.map(g=>`${g.name} · ${Math.round(g.memory_mb/1024)} GB 显存`).join('；')||'未检测到 NVIDIA 显卡')+'。视频编码：'+h.encoder+'。AI 阶段会单独检测并在需要时回退 CPU。';}).catch(()=>{$('hardware26').textContent='硬件检测暂不可用，将优先使用 CPU。';});
  load26().catch(e=>toast(e.message,true));
})();
