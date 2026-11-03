'use strict';
// Existing packages are separate from video sources: merging never decodes video.
let materialRows=[],materialQueue=[],materialLoading=false,materialRequest=null,materialPicker=null;
const materialHealthCache=new Map();
const materialHost=document.createElement('section');materialHost.className='material-browser';
materialHost.innerHTML=`<div class="material-toolbar"><input id="materialSearch" type="search" placeholder="搜索素材标题" aria-label="搜索已有素材"><button id="materialImport" class="subtle">导入素材文件夹</button><button id="materialRefresh" class="text-btn">刷新</button><button id="materialCancelPicker" class="text-btn" hidden>取消选择</button></div><details><summary>粘贴文件夹位置</summary><textarea id="materialPaths" rows="3" placeholder="每行一个素材文件夹，也可选包含多份资料的上级目录"></textarea><button id="materialImportPaths" class="subtle">加入文件夹</button></details><p id="materialNotice" class="mini-note" role="status">正在读取已有资料…</p><div id="materialList" class="material-list"></div><h3>待合并顺序</h3><div id="materialQueue"></div><label class="field">合集名称<input id="materialTitle" value="联合AI分析资料" maxlength="120"></label><p class="mini-note">保留每集标题与分界，直接复用已有资料，另存新文件夹。无需重新采集或解析视频。</p><button id="materialMerge" class="primary wide">合并所选 AI 资料</button>`;
$('analysisJobField').before(materialHost);$('analysisJobField').hidden=true;
const materialLegacy=document.createElement('details');materialLegacy.innerHTML='<summary>已有视频：重新分析</summary>';
materialHost.append(materialLegacy);materialLegacy.append($('analysisJobField'));$('analysisJobField').hidden=false;
const materialHealthDialog=document.createElement('dialog');materialHealthDialog.className='material-health-dialog';materialHealthDialog.innerHTML='<div class="panel-head"><h2>AI 资料检查</h2><button class="text-btn" data-material-close>关闭 ×</button></div><div class="padded" data-material-report></div>';document.body.append(materialHealthDialog);
materialHealthDialog.querySelector('[data-material-close]').onclick=()=>materialHealthDialog.close();

function materialLayout(){
  const mode=$('analysisSource').value==='job',merge=mode&&!materialLegacy.open;
  document.querySelector('.analysis-layout').classList.toggle('material-merge-mode',merge);
  const purpose=document.querySelector('.purpose-picker');purpose.hidden=true;purpose.previousElementSibling.hidden=true;
  $('analysisImmediate').hidden=merge;$('analysisStart').hidden=merge;
  const shortcut=document.querySelector('[data-import-folder="analysisSource"]');if(shortcut)shortcut.hidden=mode;
}
function materialMode(){const mode=$('analysisSource').value==='job';materialHost.hidden=!mode;$('analysisJobField').hidden=false;materialLayout();if(mode)refreshMaterials().catch(e=>toast(e.message,true));}
$('analysisSource').addEventListener('change',materialMode);materialLegacy.addEventListener('toggle',materialLayout);
function renderMaterialList(){
  const query=$('materialSearch').value.trim().toLowerCase();
  const rows=materialRows.filter(m=>m.title.toLowerCase().includes(query));
  $('materialList').innerHTML=rows.map(m=>`<article class="material-row"><label><input type="checkbox" data-material-select="${m.id}" ${materialQueue.some(q=>q.id===m.id)?'checked':''} ${m.available?'':'disabled'}><span><strong>${html(m.title)}</strong><small>${html(m.status)} · ${m.count} 个上传文件${m.review_count?' · '+m.review_count+' 处文字建议核对':''}${m.content_missing?' · 有内容未完成提取':''}${m.episode?' · 第'+m.episode+'集':''}</small><small title="${html(m.directory)}">${html(m.directory)}</small></span></label><div><button class="text-btn" data-material-health="${m.id}">检查资料</button>${m.available?'':`<button class="text-btn" data-material-locate="${m.id}">重新定位</button>`}</div></article>`).join('')||'<p class="muted">没有找到资料。可导入以前导出的素材文件夹。</p>';
  $('materialQueue').innerHTML=materialQueue.map((q,i)=>`<article class="material-selected"><strong>${i+1}. ${html(q.title)}</strong><label>集数<input type="number" min="1" max="10000" value="${q.episode??''}" placeholder="保留原集数" data-material-episode="${q.id}" aria-label="${html(q.title)}集数"></label><input value="${html(q.episode_title||'')}" placeholder="集标题（留空沿用原有标题）" data-material-title="${q.id}" aria-label="集标题"><div class="button-row"><button class="text-btn" data-material-up="${q.id}" ${i===0?'disabled':''}>上移</button><button class="text-btn" data-material-down="${q.id}" ${i===materialQueue.length-1?'disabled':''}>下移</button><button class="text-btn" data-material-remove="${q.id}">移除</button></div></article>`).join('')||'<p class="muted">勾选上方资料，可把新旧素材一起加入。</p>';
  $('materialMerge').disabled=!materialQueue.length;
}
async function refreshMaterials(){
  if(materialLoading)return;materialLoading=true;
  try{materialRows=(await api('/api/materials/catalog')).materials;renderMaterialList();$('materialNotice').textContent=`${materialRows.filter(m=>m.available).length} 份可合并资料 · 已选 ${materialQueue.length} 份`;
    const byJob=new Map(materialRows.filter(m=>m.job_id).map(m=>[m.job_id,m]));
    for(const article of document.querySelectorAll('#jobs .job')){const id=article.querySelector('[data-id]')?.dataset.id,m=byJob.get(id);if(!m)continue;const slot=article.querySelector('[data-material-status]');if(slot){slot.textContent=`${m.available?'上传文件齐全':'资料需检查'} · 共 ${m.count} 个上传文件${m.review_count?' · '+m.review_count+' 处文字建议核对':''}${m.content_missing?' · 内容需补充':''}`;slot.onclick=()=>showMaterialHealth(m.id).catch(e=>toast(e.message,true));}}
  }finally{materialLoading=false;}
}
async function showMaterialHealth(id){
  const result=await api(`/api/materials/${id}/health`);materialHealthCache.set(id,result);
  const body=materialHealthDialog.querySelector('[data-material-report]');
  body.innerHTML=`<h3>${html(result.title)}</h3><p><strong>${html(result.file_status)} · 共 ${result.count} 个上传文件</strong> <span class="help-dot" tabindex="0" title="文件数量表示需要一起交给 AI 的文件总数，不是缺失数量。拼图概览与原尺寸细节图互相补充；仅上传其中一部分会缺少资料。">!</span></p><p>${Object.entries(result.breakdown).map(([k,v])=>`${html(k)} ${v}`).join(' · ')}</p><p>${result.review_count} 处建议核对${result.content_missing?' · 有内容未完成提取':''}。文件齐全不代表识别准确或采样无遗漏。</p>${result.missing.length?`<h4>缺少 ${result.missing.length} 个文件</h4><pre>${html(result.missing.join('\n'))}</pre><button class="subtle" data-health-locate>重新定位资料文件夹</button>`:''}<div class="material-issues">${result.issues.map(i=>`<p><b>${i.severity==='missing_content'?'内容缺失':'建议核对'}</b> ${html(i.message)}${i.detail?` <a href="/api/materials/${id}/preview/${i.detail.split('/').map(encodeURIComponent).join('/')}" target="_blank" rel="noopener">查看细节图 ↗</a>`:''}</p>`).join('')||'<p>没有已记录的处理问题，仍请核对识别与采样误差。</p>'}</div>`;
  body.querySelector('[data-health-locate]')?.addEventListener('click',()=>pickMaterialFolder(id).catch(e=>toast(e.message,true)));
  if(!materialHealthDialog.open)materialHealthDialog.showModal();
}
async function importMaterialPaths(paths,replace_id=null){
  const result=await api('/api/materials/import',{method:'POST',body:{paths,replace_id}});
  if(replace_id){materialQueue=materialQueue.filter(q=>q.id!==replace_id);materialHealthCache.delete(replace_id);}
  for(const m of result.materials)if(m.available&&!materialQueue.some(q=>q.id===m.id))materialQueue.push({...m,episode:null,episode_title:''});
  materialRequest=null;await refreshMaterials();
  $('materialNotice').textContent=result.materials.length?`找到 ${result.materials.length} 份资料，已加入可用项目。${result.skipped.map(s=>s.reason).join('；')}`:'未找到资料包，请选择含“一起上传给AI”的文件夹，或该文件夹本身。';
}
async function pickMaterialFolder(replaceId=null){
  if(materialPicker)throw new Error('选择窗口已打开，可先取消选择');
  const button=$('materialImport');button.disabled=true;$('materialCancelPicker').hidden=false;
  try{const task=await api('/api/pickers',{method:'POST',body:{kind:'folder'}});materialPicker=task.id;
    let state;do{await new Promise(r=>setTimeout(r,400));state=await api(`/api/pickers/${task.id}`);}while(state.status==='pending');
    if(state.status==='error')throw new Error(state.error||'选择未完成，请重试');
    if(state.status==='done'&&state.paths.length)await importMaterialPaths(state.paths,replaceId);
  }finally{materialPicker=null;button.disabled=false;$('materialCancelPicker').hidden=true;}
}
$('materialSearch').oninput=renderMaterialList;
bind('materialImport',()=>pickMaterialFolder());bind('materialRefresh',()=>refreshMaterials());
bind('materialImportPaths',()=>busy($('materialImportPaths'),()=>importMaterialPaths($('materialPaths').value)));
bind('materialCancelPicker',async()=>{if(materialPicker)await api(`/api/pickers/${materialPicker}/cancel`,{method:'POST'});});
materialHost.addEventListener('change',event=>{
  const d=event.target.dataset,id=d.materialSelect||d.materialEpisode||d.materialTitle;
  if(d.materialSelect){if(event.target.checked&&!materialQueue.some(q=>q.id===id)){const m=materialRows.find(m=>m.id===id);materialQueue.push({...m,episode:null,episode_title:''});}else if(!event.target.checked)materialQueue=materialQueue.filter(q=>q.id!==id);renderMaterialList();}
  else if(d.materialEpisode){const q=materialQueue.find(q=>q.id===id);q.episode=event.target.value?Number(event.target.value):null;}
  else if(d.materialTitle)materialQueue.find(q=>q.id===id).episode_title=event.target.value;
  materialRequest=null;
});
materialHost.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;const d=button.dataset;
  if(d.materialHealth)showMaterialHealth(d.materialHealth).catch(e=>toast(e.message,true));
  if(d.materialLocate)pickMaterialFolder(d.materialLocate).catch(e=>toast(e.message,true));
  const id=d.materialUp||d.materialDown||d.materialRemove;
  if(id){const i=materialQueue.findIndex(q=>q.id===id);if(d.materialRemove)materialQueue.splice(i,1);else{const j=i+(d.materialUp?-1:1);[materialQueue[i],materialQueue[j]]=[materialQueue[j],materialQueue[i]];}materialRequest=null;renderMaterialList();}
});
$('materialTitle').oninput=()=>materialRequest=null;
bind('materialMerge',()=>busy($('materialMerge'),async()=>{
  if(!materialQueue.length)throw new Error('请先勾选资料');
  const body={items:materialQueue.map(q=>({id:q.id,episode:q.episode,episode_title:q.episode_title})),title:$('materialTitle').value.trim()||'联合AI分析资料',output:$('output').value};
  const signature=JSON.stringify(body);if(!materialRequest||materialRequest.signature!==signature)materialRequest={signature,id:crypto.randomUUID()};
  await api('/api/materials/merge',{method:'POST',body:{...body,request_id:materialRequest.id}});await refresh();goto('library');toast('已提交合并，可在素材列表查看进度');
}));
const materialRenderBefore=renderJobs;
renderJobs=function(){materialRenderBefore();for(const article of document.querySelectorAll('#jobs .job')){
  const id=article.querySelector('[data-id]')?.dataset.id,j=jobs.find(x=>x.id===id);if(!j)continue;
  const actions=article.querySelector('.job-actions');
  if(j.ai_package&&!j.superseded_by&&!j.materials_deleted){const b=document.createElement('button');b.className='text-btn';b.dataset.materialStatus='';b.textContent=`检查上传资料 · 共 ${j.ai_package.files.length} 个文件`;b.onclick=async()=>{try{await refreshMaterials();const m=materialRows.find(x=>x.job_id===id);if(m)await showMaterialHealth(m.id);}catch(e){toast(e.message,true);}};actions.append(b);}
  if(j.ai_package&&j.status==='done'&&!j.superseded_by&&!j.materials_deleted){const b=document.createElement('button');b.className='subtle';b.textContent='加入资料合并';b.onclick=async()=>{try{await refreshMaterials();const m=materialRows.find(x=>x.job_id===id);if(!m?.available)throw new Error('资料需先检查或重新定位');if(!materialQueue.some(q=>q.id===m.id))materialQueue.push({...m,episode:null,episode_title:''});$('analysisSource').value='job';updateAnalysisSource();materialMode();renderMaterialList();goto('analysis');}catch(e){toast(e.message,true);}};actions.append(b);}
  if(j.kind==='material-merge'&&!activeStatus(j.status)&&j.status!=='done'){const b=document.createElement('button');b.className='text-btn';b.textContent='重试合并';b.onclick=()=>busy(b,async()=>{await api(`/api/materials/merge/${id}/retry`,{method:'POST',body:{request_id:crypto.randomUUID()}});await refresh();}).catch(e=>toast(e.message,true));actions.append(b);}
}if(currentPage==='library')refreshMaterials().catch(()=>{});};
materialMode();
