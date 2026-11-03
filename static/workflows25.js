'use strict';
// Page structure lives here; processing and persistence use the shared contracts.
const purposePicker=document.querySelector('.purpose-picker');
purposePicker.hidden=true;purposePicker.previousElementSibling.hidden=true;
$('analysisStart').textContent='生成 AI 资料包';
document.querySelector('#page-extract h1').textContent='按自己的规则，提取视频画面。';
document.querySelector('#page-extract .page-heading p').textContent='默认只生成图片。按秒、每秒张数、每隔 N 帧或全部帧；可裁剪、去重及另存音轨。';
for(const id of ['analysisTranscriptFile','analysisStartTime','analysisDuration']){
  const field=$(id).closest('label');document.querySelector('#page-analysis details.advanced').append(field);
}
const aiPreset=$('analysisPreset').closest('label');
aiPreset.after(cellsField);
document.querySelector('#page-analysis details.advanced').querySelector('summary').textContent='高级设置 · 完整分镜策略';
const combineChoice=document.createElement('label');combineChoice.className='field';
combineChoice.innerHTML='多视频资料包<select id="packageLayout"><option value="separate">分别生成资料包</option><option value="combined">生成联合资料包</option></select><small>保留每集标题与分界，不合成 MP4。</small>';
$('analysisStart').before(combineChoice);
combineChoice.querySelector('select').onchange=()=>{workflowSettings.board.ai_package.combined=$('packageLayout').value==='combined';syncExportPanels();};
const materialLayoutBefore25=materialLayout;
materialLayout=function(){materialLayoutBefore25();combineChoice.hidden=$('analysisSource').value==='job'&&!materialLegacy.open;};
// Keep local, link and existing-material merge controls in the same source column.
$('episodeCombined').closest('label').hidden=true;
const subtitlesPage=document.createElement('section');subtitlesPage.id='page-subtitles';subtitlesPage.className='page';subtitlesPage.hidden=true;
subtitlesPage.innerHTML='<div class="page-heading"><div><h1>字幕区域截图长图</h1><p>截取已经画在视频中的字幕区域，并拼成长图。采样可能漏掉短暂字幕；不等于 TXT / SRT 提取，也不会去除视频字幕。</p></div></div><div class="two-columns"><section class="panel padded"><h2>选择本机视频</h2><div id="subtitleLocalSources" class="local-source-panel"></div></section><section class="panel padded" id="subtitleOptions"><h2>字幕截图设置</h2><button id="subtitleStart" class="primary wide">生成字幕区域截图长图</button></section></div>';
$('page-analysis').after(subtitlesPage);
mountSourcePanel($('subtitleLocalSources'),'subtitles');
for(const id of ['analysisBandMode','analysisBandTop','analysisBandBottom','analysisStripStep','analysisStripCount'])$('subtitleStart').before($(id).closest('label'));
const subtitleSpeed=document.createElement('label');subtitleSpeed.className='field';subtitleSpeed.innerHTML='处理速度<select id="subtitlePerformance"><option value="auto">自动</option><option value="low">低占用</option><option value="balanced">均衡</option><option value="high">高性能</option></select>';$('subtitleStart').before(subtitleSpeed);
bind('subtitleStart',()=>busy($('subtitleStart'),()=>{workflowSettings.subtitles.performance=$('subtitlePerformance').value;return submitLocalBatch('subtitles',{transcript:'off',ocr:false,band:$('analysisBandMode').value==='manual'?[numberValue('analysisBandTop')/100,numberValue('analysisBandBottom')/100]:null,strip_step:numberValue('analysisStripStep'),strip_count:numberValue('analysisStripCount')});}));
renderLocalSources();
// Imported copies belong to acquisition. Both processing pages read the shared source list.
const copyPanel=$('extractUploadPanel');
const copyHost=document.createElement('section');copyHost.className='panel padded section-space';copyHost.innerHTML='<h2>导入视频副本</h2><p class="mini-note">将视频复制到保存位置。导入后可送往视频抽帧或 AI 分析。</p><button id="importVideoCopy" class="primary">导入副本</button>';
$('page-import').append(copyHost);copyHost.querySelector('button').before(copyPanel);copyPanel.hidden=false;
$('extractMode').querySelector('[value="upload"]').remove();
bind('importVideoCopy',()=>busy($('importVideoCopy'),async()=>{const file=$('videoFile').files[0];if(!file)throw new Error('先选择要复制的视频');const data=new FormData();data.append('file',file);data.append('output',$('output').value);data.append('options',JSON.stringify({extract:false}));const result=await api('/api/upload-video',{method:'POST',body:data,raw:true});await refresh();goto('library');toast('副本已导入，可选择抽帧或 AI 分析');}));
const oldUpdateExtractMode25=updateExtractMode;
updateExtractMode=function(){oldUpdateExtractMode25();copyPanel.hidden=false;};
const flowFields={board:['analysisSource','analysisMode','analysisPreset','analysisTranscript','analysisTranscriptFile','analysisStartTime','analysisDuration','analysisStrategy','analysisPickMode','analysisMaxFrames','analysisMaxSheets','analysisCellsPerSheet','analysisBatch','analysisHeight','analysisDetail','analysisOCR','analysisHotwords','analysisTerms','analysisIntegral','analysisIntegralSeconds','analysisScanGap','analysisTierMap'],rules:['extractMode','frameMode','frameValue','frameStart','frameDuration','frameLimit','streamCropEnabled','streamCropX','streamCropY','streamCropW','streamCropH'],subtitles:['analysisBandMode','analysisBandTop','analysisBandBottom','analysisStripStep','analysisStripCount','subtitlePerformance']};
document.addEventListener('change',event=>{for(const [kind,ids] of Object.entries(flowFields))if(ids.includes(event.target.id)){workflowSettings[kind].fields[event.target.id]=event.target.type==='checkbox'?event.target.checked:event.target.value;saveLocalState();}});
function restoreWorkflowFields(){for(const [kind,ids] of Object.entries(flowFields))for(const id of ids){const el=$(id),value=workflowSettings[kind].fields?.[id];if(el&&value!==undefined){if(el.type==='checkbox')el.checked=value;else el.value=value;}}updateAnalysisSource();updateExtractMode();materialMode();syncExportPanels();}
function updateWorkflowStartLabels(){
  const o=workflowSettings.rules.outputs;
  if(!$('extractBtn').disabled)$('extractBtn').textContent='开始生成'+[['frames','图片'],['audio','音轨'],['clips','分段小视频']].filter(([k])=>o[k]).map(([,v])=>v).join('、');
  if($('packageLayout'))$('packageLayout').value=workflowSettings.board.ai_package.combined?'combined':'separate';
}
// Cleanup selection survives refresh; review always comes from server ownership checks.
const cleanupSelection=new Set();
const cleanupBar=document.createElement('div');cleanupBar.className='button-row cleanup-toolbar';cleanupBar.innerHTML='<button id="selectCleanupJobs" class="subtle">选择本页可清理任务</button><button id="batchCleanup" class="subtle">预览清理所选素材</button><label class="checkbox-line"><input id="showCleaned" type="checkbox">展开已清理 / 已被替换</label>';$('jobs').before(cleanupBar);
$('showCleaned').onchange=()=>renderJobs();
$('selectCleanupJobs').onclick=()=>{for(const el of document.querySelectorAll('[data-clean-select]')){el.checked=true;cleanupSelection.add(el.dataset.cleanSelect);}};
const renderJobsBefore25=renderJobs;
renderJobs=function(){renderJobsBefore25();for(const article of document.querySelectorAll('#jobs .job')){const id=article.querySelector('[data-id]')?.dataset.id,j=jobs.find(x=>x.id===id);if(!j||activeStatus(j.status)||j.materials_deleted)continue;const label=document.createElement('label');label.className='checkbox-line';label.innerHTML=`<input type="checkbox" data-clean-select="${id}" ${cleanupSelection.has(id)?'checked':''}>清理选择`;label.querySelector('input').onchange=e=>e.target.checked?cleanupSelection.add(id):cleanupSelection.delete(id);article.querySelector('.job-actions').prepend(label);}};
let cleanIds25=[];
async function previewCleanup25(ids){cleanIds25=ids;cleanDialog.querySelector('[data-clean-note]').textContent='正在核对生成文件、用户改动和共享引用，较多素材可能需要一些时间。';cleanDialog.querySelector('[data-clean-files]').textContent='';cleanDialog.querySelector('[data-clean-submit]').disabled=true;if(!cleanDialog.open)cleanDialog.showModal();const result=await api('/api/materials/cleanup-preview',{method:'POST',body:{ids},timeoutMs:120000});cleanDialog.querySelector('[data-clean-note]').textContent=`将把 ${result.files.length} 个生成文件（${(result.bytes/1048576).toFixed(1)} MB）放入系统回收站，并从默认列表移除所选任务。跳过 ${result.skipped.length} 项；无法回收时保留文件并提示。`;cleanDialog.querySelector('[data-clean-files]').textContent=result.files.map(x=>x.path).join('\n')+'\n\n跳过：\n'+result.skipped.map(x=>`${x.name}：${x.reason}`).join('\n');cleanDialog.querySelector('[data-clean-submit]').disabled=!result.ids.length;cleanDialog.querySelector('[data-clean-submit]').textContent='移入回收站并移出默认列表';if(!cleanDialog.open)cleanDialog.showModal();}
previewCleanup=id=>previewCleanup25([id]);
$('batchCleanup').onclick=()=>previewCleanup25([...cleanupSelection]).catch(e=>toast(e.message,true));
cleanDialog.querySelector('[data-clean-submit]').onclick=()=>busy(cleanDialog.querySelector('[data-clean-submit]'),async()=>{const result=await api('/api/materials/cleanup-batch',{method:'POST',body:{ids:cleanIds25},timeoutMs:300000});cleanDialog.close();cleanupSelection.clear();await refresh();toast(`已回收 ${result.deleted} 项；跳过 ${result.skipped.length} 项${result.errors.length?'；回收失败：'+result.errors[0].reason:''}`,!!result.errors.length);}).catch(e=>toast(e.message,true));
restoreWorkflowFields();attachOptionHelp(subtitlesPage);

async function submitRuleLinks25(){
  activateWorkflow('rules');const state=readExportSettings($('extractExportChoices'));
  const links=readOnlineLinks($('streamUrls').value);if(!links.length)throw new Error('请先粘贴视频链接');
  const result=await api('/api/batches',{method:'POST',body:{sources:['online'],online_sources:links.map(input=>({input})),kind:'rules',workflow:'rules',
    output:$('output').value,outputs:state.outputs,dedup:state.dedup,segments:state.segments,performance:state.performance,frames:frameSettings(),
    analysis:{mode:'direct',login:$('streamLogin').checked,cookie_id:$('streamCookie').value,max_height:Number($('streamHeight').value),transcript:'off'},request_id:crypto.randomUUID()}});
  lastBatch=result.id;saveLocalState();await refresh();goto('library');
}
