'use strict';
// Keep the two workflows on the same settings and API. Help controls never
// toggle their parent labels, including checkboxes, on pointer or keyboard use.
const optionHelp={
  analysisPurpose_board:'把视频按镜头选出代表画面，与完整台词对应，生成供 AI 分析的拼图和索引。多视频可联合输出。画面采样不是逐帧无损编码。',
  analysisPurpose_rules:'按固定秒数、帧率、每隔 N 帧或全部原始帧导出图片。适合需要可预测截图密度时使用，也可以按所选内容生成 AI 阅读包。',
  analysisPurpose_subtitles:'提取的是视频里已经画出来的硬字幕区域：先按间隔截图字幕带，再拼成一张长图。它不会去掉视频中的字幕。开启 OCR 后可尝试把图中文字识别为文本；这里的长图有间隔和条数限制，可能漏掉字幕，不等于全片完整转写。需要更完整的口述和屏幕文字时选择“给 AI 看懂视频”及 AI 阅读包。',
  analysisStrategy:'compact：按镜头重要程度分配大小，适合日常。full：更多大画面，细节优先。standard：标准分配。skim：快速浏览，长镜头补帧较少。brief：尽量压成单图，可能合并镜头。需要完整信息时避免 skim/brief，并取消总帧数和总拼图限额。',
  analysisPickMode:'镜头中点：取镜头中间的画面，速度快。清晰帧：比较三个候选画面的清晰度，更慢。典型帧：选择更能代表镜头的画面。',
  analysisMaxFrames:'一个分析时间段内最多选取多少个代表画面；0 表示不限制。限额可能合并镜头。它是总帧数限制，不是每张拼图的格子数。',
  analysisMaxSheets:'一个分析时间段的自动拼图预算；0 不限制。预算可能导致镜头合并。填写“每张拼图最多画面”后，可另增拼图来保存所有已经选出的画面。',
  analysisCellsPerSheet:'填 1 就是一张图一个画面；填 4 就是每张最多 4 个；填 9 就是每张最多 9 个。最后一张可以不足。0 使用自动排布。数量越多，单个画面和小字通常越小。AI 阅读包为保证可读性每张实际不超过 16 格，所有画面均保留。',
  analysisBatch:'将长视频分成多少秒一个分析窗口，便于进度、引用和处理。0 不分窗。这不等于输出分段 MP4；生成小视频请使用“时间分段”。',
  analysisHeight:'只影响在线来源的请求清晰度：480p 更省流量，720p/1080p 通常更清楚，最高可用取平台可提供的版本。不会改变导入原视频。登录浏览器回退可能只能使用当前播放器清晰度。',
  analysisDetail:'旧版分镜清单中每格的台词显示量：简短 60 字、适中 120 字、详细 400 字。完整台词仍单独保留；新 AI 阅读包的完整内容不按这项截断。',
  analysisOCR:'硬字幕是已经画进视频像素中的文字，例如画面底部直接出现的解说字幕，通常不能像独立字幕轨一样直接导出。OCR 是把这些画面文字识别成文本。这里沿用原项目，最多抽查部分画面校正语音台词，不是全片硬字幕提取。AI 阅读包的“识别画面文字”会另外扫描全画面的文字变化；两者都可能识别有误。',
  analysisHotwords:'为兼容的语音识别服务提供人名、品牌、专业词，帮助识别。本地 SenseVoice 不支持热词偏置；填写后不会让本地识别自动学会这些词。',
  analysisTerms:'确定性替换识别结果中的错误术语，格式如 {"错误词":"正确词"}。只填写确认无误的替换，避免把正常内容替换掉。',
  analysisIntegral:'长镜头没有切换镜头时，仍会根据画面持续变化累计补充代表帧，帮助看见 PPT 翻页、操作步骤或画面变化。关闭后长镜头可能只有一个代表画面。',
  analysisIntegralSeconds:'画面变化累计达到此量时补一帧。值小通常补得更密，值大更省图；不是严格“每几秒一帧”。固定间隔请用规则抽帧。',
  analysisScanGap:'合并距离过近的镜头切点，单位为秒；0 不主动合并。增大可减少图片，但可能忽略快速切换的镜头。',
  analysisTierMap:'调整自动格子大小档位，如 {"5":4} 表示原来的 1/5 档改成更大的 1/4 档。数字越小画面越大。手动设置每张画面上限时改用等大格子，该映射主要影响自动排布。',
  analysisBandMode:'用于字幕长图的画面裁剪。自动：寻找底部字幕区域，失败时用底部默认区域。手动：按下方百分比裁剪。不是擦除或自动翻译硬字幕。',
  analysisBandTop:'字幕裁剪区域的上边界，按原画面高度百分比，0 是画面顶部。必须小于底部百分比。',
  analysisBandBottom:'字幕裁剪区域的下边界，100 是画面底部。区域太小可能切掉字幕，太大则包含较多无关画面。',
  analysisStripStep:'生成字幕长图时每隔几秒截取字幕区域。间隔越大越省图，也越可能漏掉短暂字幕。它不会对每个视频帧都进行文字识别。',
  analysisStripCount:'字幕长图最多拼接多少条字幕区域；超过后不再追加。不是分镜拼图的画面数量上限；全片文字建议使用 AI 阅读包的画面文字扫描。',
  'ai_package.enabled':'开启后另外生成“一起上传给AI”文件夹：完整台词只写一次、编号画面和必要细节一起提供。原始索引、日志及额外素材留在文件夹外。关闭则沿用旧版导出结构。',
  'ai_package.combined':'按待处理列表顺序把全部视频的完整内容汇总到一个联合 AI 资料包，保留视频标题、各自原片时间和联合时间轴。只要上传联合包的“一起上传给AI”内所有文件；不会合成新 MP4。',
  'ai_package.screen_text':'在完整画面上用本地 OCR 读取字幕、PPT、价格、表格等文字。与口述台词并列保留。数字和专有名词可能识别有误；细字或低置信度会附必要细节图。关闭时仍保留画面供 AI 阅读。',
  'ai_package.ocr_threads':'平衡加速最多用两条计算线程，保守模式用一条；均为单任务处理，保留全部已采样画面和文字，识别缓存最多占用 8 MiB。旧任务修改后重新生成也可复用完全相同画面的文字结果。',
  'ai_package.scan_seconds':'画面有变化时额外采样；静态片段至少按这个间隔检查。1 秒较均衡，0.1 秒更密更慢。静态采样仍可能遗漏非常短暂的文字/动作；不能保证逐帧无损。',
  'ai_package.batch_files':'只生成分批上传说明，不会删除或省略超过数量的文件。例如每批 10 个，需要把全部批次都传完再让 AI 汇总。',
  'outputs.frames':'按规则导出单独的画面图片，便于编辑、检查和进一步处理。不会因此自动生成拼图。',
  'outputs.audio':'另存可播放的音轨文件。仅选音轨时不抽帧，也不做语音识别。原视频没有声音会明确提示。',
  'outputs.puzzles':'将多个按时间排序的画面排在一张图片里，减少 AI 输入图片次数。每张画面数量可在上方自定义。小字可能需要必要细节图。',
  'outputs.transcript':'保存现成字幕或语音识别得到的文字，保留原视频时间及分段内时间。关闭语音识别且无现成字幕时可能无法生成台词。',
  'outputs.clips':'按下方分段秒数重新编码输出 MP4 小视频，保留最后不足一段的部分。会增加新的视频文件，但不会修改导入的原视频。',
  'outputs.ai':'附带供 AI 理解画面、时间和台词的索引。开启 AI 阅读包后，另外生成必须一起上传的内容。单纯素材导出可关闭此项。',
  'outputs.zip':'另外生成 ZIP。新 AI 阅读包只打包必须上传的内容，原始索引、额外音轨和备查图片不混入 ZIP。',
  'dedup.enabled':'只去重导出的单帧图片，不删除原始输入，不去掉整张拼图，不改变台词时间轴。按视频、按时间段独立比较。',
  'dedup.threshold':'完全相同按像素内容比较。相似模式的阈值越大越容易判为重复：保守 4、适中 8、宽松 12、自定义 0–16。过大可能合并字幕或细节变化。',
  'segments.enabled':'按指定时间边界整理素材，或者生成分段小视频；最后不足一段的尾部保留。仅勾选整理不产生新 MP4。',
  'segments.seconds':'每段持续多少秒，例如 10 就是 0–10、10–20……。各类素材共用分段边界，原视频不会被剪坏。',
  'segments.organize':'开启后为每个片段建立独立素材子文件夹；关闭则将片段编号放入文件名，仍保留时间分界。',
};
function attachOptionHelp(scope=document){
  for(const control of scope.querySelectorAll('details.advanced input,details.advanced select,details.advanced textarea,.export-options [data-setting],.export-options [data-dedup-preset],input[name="analysisPurpose"]')){
    if(control.dataset.helpInstalled)return;control.dataset.helpInstalled='1';
    const label=control.closest('label');if(!label)continue;
    const name=control.id||control.dataset.setting||(control.name==='analysisPurpose'?'analysisPurpose_'+control.value:'dedup.threshold');
    const text=optionHelp[name]||[...label.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join(' ')+'：'+(control.options?[...control.options].map(o=>o.textContent).join('；'):'调整此功能的处理参数。');
    const tip=document.createElement('button');tip.type='button';tip.className='option-help';tip.textContent='!';tip.setAttribute('aria-label','功能说明');tip.dataset.help=text;
    for(const event of ['click','pointerdown','mousedown'])tip.addEventListener(event,e=>{e.preventDefault();e.stopPropagation();});
    tip.addEventListener('mouseenter',()=>showOptionHelp(tip));tip.addEventListener('focus',()=>showOptionHelp(tip));
    tip.addEventListener('mouseleave',hideOptionHelp);tip.addEventListener('blur',hideOptionHelp);tip.addEventListener('keydown',e=>{if(e.key==='Escape')hideOptionHelp();if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopPropagation();showOptionHelp(tip);}});
    const first=label.querySelector('input,select,textarea');
    if(first===label.firstElementChild&&['checkbox','radio'].includes(first.type))label.append(tip);else {
      const caption=document.createElement('span');caption.className='field-caption';
      for(const node of [...label.childNodes])if(node.nodeType===3)caption.append(node);
      caption.append(tip);label.insertBefore(caption,first);
    }
  }
}
const helpBubble=document.createElement('div');helpBubble.className='option-help-bubble';helpBubble.id='optionHelpBubble';helpBubble.setAttribute('role','tooltip');helpBubble.hidden=true;document.body.append(helpBubble);
let currentHelp=null;
function showOptionHelp(button){if(currentHelp)currentHelp.removeAttribute('aria-describedby');currentHelp=button;(button.closest('dialog[open]')||document.body).append(helpBubble);button.setAttribute('aria-describedby',helpBubble.id);helpBubble.textContent=button.dataset.help;helpBubble.hidden=false;const r=button.getBoundingClientRect();helpBubble.style.left=Math.max(8,Math.min(innerWidth-helpBubble.offsetWidth-8,r.left))+'px';helpBubble.style.top=Math.max(8,Math.min(innerHeight-helpBubble.offsetHeight-8,r.bottom+8))+'px';}
function hideOptionHelp(){helpBubble.hidden=true;currentHelp?.removeAttribute('aria-describedby');currentHelp=null;}
window.addEventListener('scroll',()=>{if(currentHelp)requestAnimationFrame(()=>{if(currentHelp?.matches(':hover,:focus'))showOptionHelp(currentHelp);else hideOptionHelp();});},true);window.addEventListener('resize',hideOptionHelp);
const cellsField=document.createElement('label');cellsField.className='field';cellsField.innerHTML='每张拼图最多画面<input id="analysisCellsPerSheet" type="number" value="0" min="0" max="10000" step="1"><small>填写 1、4、9 等整数；0 自动。超出会另起一张，最后一张允许不足。</small>';$('analysisMaxSheets').closest('.inline-fields').after(cellsField);
for(const panel of document.querySelectorAll('#analysisExportChoices')){
  panel.insertAdjacentHTML('beforeend','<details class="advanced"><summary>AI 阅读包高级设置</summary><label class="checkbox-line"><input type="checkbox" data-setting="ai_package.screen_text">识别画面文字（包含硬字幕、价格、PPT）</label><label class="field">画面扫描补充间隔（秒）<input type="number" min="0.1" max="30" step="0.1" data-setting="ai_package.scan_seconds"></label><label class="field">建议每批上传文件数<input type="number" min="1" max="1000" step="1" data-setting="ai_package.batch_files"></label><p class="mini-note">必须上传“一起上传给AI”内全部文件；静态采样与 OCR 可能遗漏或误识别，报告会列出已知问题。</p></details>');
}
syncExportPanels();attachOptionHelp();
// The upload order is visible and editable, and is used unchanged by the API.
for(const host of document.querySelectorAll('.local-source-panel'))host.addEventListener('click',e=>{const button=e.target.closest('[data-move-source]');if(!button)return;const at=localSources.findIndex(s=>s.id===button.dataset.id),to=at+Number(button.dataset.moveSource);if(at>=0&&to>=0&&to<localSources.length){[localSources[at],localSources[to]]=[localSources[to],localSources[at]];renderLocalSources();}});
const renderSourcesBefore22=renderLocalSources;
renderLocalSources=function(){renderSourcesBefore22();for(const host of document.querySelectorAll('.local-source-panel'))for(const [i,row] of [...host.querySelectorAll('.source-row')].entries()){const actions=row.querySelector('.button-row');const id=localSources[i].id;actions.insertAdjacentHTML('afterbegin',`<button type="button" class="text-btn" data-move-source="-1" data-id="${id}" ${i===0?'disabled':''} aria-label="上移视频">↑</button><button type="button" class="text-btn" data-move-source="1" data-id="${id}" ${i===localSources.length-1?'disabled':''} aria-label="下移视频">↓</button>`);}};renderLocalSources();
const regenerateCheck=document.createElement('label');regenerateCheck.className='checkbox-line';regenerateCheck.innerHTML='<input type="checkbox" id="replaceGeneratedMaterials">覆盖本任务上次生成的素材文件夹（原始视频保留）';exportDialog.querySelector('[data-export-submit]').before(regenerateCheck);
exportDialog.querySelector('.padded>p').textContent='可先修改分镜参数，再重新生成。覆盖仅在旧目录没有移动、修改或新增文件时执行；否则另存并保留旧结果。';
const originalOpenExport22=openCommonExport;
let loadedRegenerationJob='';
function regenerationIsBoard(job){return (job?.options?.workflow||job?.options?.kind||(job?.kind==='analysis'?'board':''))==='board';}
openCommonExport=function(id){originalOpenExport22(id);$('replaceGeneratedMaterials').checked=false;const job=jobs.find(j=>j.id===id),board=regenerationIsBoard(job);regenerationControls.hidden=!board;exportDialog.querySelector('.padded>p').textContent=board?'可修改分镜参数后重新生成；覆盖仅在旧目录未移动或修改时执行。':'按此流程的原参数重新生成所选内容；覆盖前会核对旧素材。';if(!board)return;const params=job?.options?.analysis||job?.options||{};const fields={analysisStrategy:'strategy',analysisPickMode:'pick',analysisMaxFrames:'max_frames',analysisMaxSheets:'max_sheets',analysisCellsPerSheet:'max_cells_per_sheet',analysisBatch:'batch_seconds',analysisHeight:'max_height',analysisDetail:'detail',analysisOCR:'ocr',analysisIntegral:'integral',analysisIntegralSeconds:'integral_seconds',analysisScanGap:'scan_gap',analysisTranscript:'transcript',analysisTranscriptFile:'transcript_file',analysisStartTime:'start',analysisDuration:'duration',analysisHotwords:'hotwords',analysisStripStep:'strip_step',analysisStripCount:'strip_count'};if(loadedRegenerationJob!==id){for(const [id,key] of Object.entries(fields)){if(params[key]===undefined)continue;if($(id).type==='checkbox')$(id).checked=params[key];else $(id).value=params[key];}if(params.terms)$('analysisTerms').value=JSON.stringify(params.terms);if(params.tier_map)$('analysisTierMap').value=JSON.stringify(params.tier_map);loadedRegenerationJob=id;}refreshRegenerationControls();};
function regenerationAnalysis(){return {strategy:$('analysisStrategy').value,pick:$('analysisPickMode').value,max_frames:numberValue('analysisMaxFrames'),max_sheets:numberValue('analysisMaxSheets'),max_cells_per_sheet:numberValue('analysisCellsPerSheet'),batch_seconds:numberValue('analysisBatch'),max_height:numberValue('analysisHeight'),detail:$('analysisDetail').value,ocr:$('analysisOCR').checked,integral:$('analysisIntegral').checked,integral_seconds:numberValue('analysisIntegralSeconds'),scan_gap:numberValue('analysisScanGap'),transcript:$('analysisTranscript').value,transcript_file:$('analysisTranscriptFile').value.trim(),start:numberValue('analysisStartTime'),duration:numberValue('analysisDuration'),hotwords:$('analysisHotwords').value,terms:jsonValue('analysisTerms'),tier_map:jsonValue('analysisTierMap')};}
const regenerationControls=document.createElement('details');regenerationControls.className='advanced';regenerationControls.innerHTML='<summary>修改分镜参数后重新生成</summary><div data-regeneration-fields></div>';regenerateCheck.before(regenerationControls);
function refreshRegenerationControls(){const host=regenerationControls.querySelector('[data-regeneration-fields]');host.replaceChildren();for(const id of ['analysisStrategy','analysisPickMode','analysisCellsPerSheet','analysisMaxFrames','analysisMaxSheets','analysisTranscript','analysisIntegral','analysisIntegralSeconds','analysisScanGap']){const original=$(id),label=original.closest('label').cloneNode(true),control=label.querySelector('input,select');label.querySelector('.option-help')?.remove();control.removeAttribute('id');delete control.dataset.helpInstalled;control.dataset.originalId=id;if(control.type==='checkbox')control.checked=original.checked;else control.value=original.value;control.oninput=control.onchange=()=>{if(control.type==='checkbox')original.checked=control.checked;else original.value=control.value;};const help=document.createElement('button');help.type='button';help.className='option-help';help.textContent='!';help.dataset.help=optionHelp[id]||'选择台词来源：自动优先字幕，没有时使用语音识别；只用字幕不会识别语音；关闭则不生成口述文本。';help.onclick=e=>{e.preventDefault();e.stopPropagation();};help.onmouseenter=help.onfocus=()=>showOptionHelp(help);help.onmouseleave=help.onblur=hideOptionHelp;(label.querySelector('.field-caption')||label).append(help);host.append(label);}}
// The existing idempotent submission remains in charge; extend its request at
// the call boundary only for this dialog, so normal submissions stay unchanged.
const apiBefore22=api;
api=async function(path,opts){if(path===`/api/exports/${commonExportId}`&&opts?.method==='POST'&&exportDialog.open){const job=jobs.find(j=>j.id===commonExportId),additions={replace_job_id:$('replaceGeneratedMaterials').checked?commonExportId:''};if(regenerationIsBoard(job))additions.analysis={...(job?.options?.analysis||{}),...regenerationAnalysis()};const data=new TextEncoder().encode(JSON.stringify(additions)),hash=await crypto.subtle.digest('SHA-256',data),suffix=[...new Uint8Array(hash)].slice(0,8).map(x=>x.toString(16).padStart(2,'0')).join('');opts={...opts,body:{...opts.body,request_id:opts.body.request_id+'_'+suffix,...additions}};}return apiBefore22(path,opts);};
// Cleanup is explicit and reviewable. A small dialog shows the exact scope;
// moved/changed files are skipped by the backend even after the preview.
const cleanDialog=document.createElement('dialog');cleanDialog.innerHTML='<div class="panel-head"><h2>清理历史生成素材</h2><button class="text-btn" data-clean-close>关闭 ×</button></div><div class="padded"><p data-clean-note></p><div data-clean-files class="cleanup-file-list"></div><button class="primary" data-clean-submit>删除列出的历史素材</button></div>';document.body.append(cleanDialog);let cleanId='';
cleanDialog.querySelector('[data-clean-close]').onclick=()=>cleanDialog.close();
async function previewCleanup(id){cleanId=id;const result=await api(`/api/materials/${id}/cleanup`);cleanDialog.querySelector('[data-clean-note]').textContent=`将删除 ${result.files.length} 个仍在原位置且未修改的软件生成文件（${(result.bytes/1048576).toFixed(1)} MB）。跳过 ${result.skipped.length} 项，原始视频保留。此操作不会查找已经移动的素材。`;cleanDialog.querySelector('[data-clean-files]').textContent=result.files.join('\n')+(result.skipped.length?'\n\n跳过：\n'+result.skipped.map(x=>`${x.name}：${x.reason}`).join('\n'):'');cleanDialog.querySelector('[data-clean-submit]').disabled=!result.files.length;cleanDialog.showModal();}
cleanDialog.querySelector('[data-clean-submit]').onclick=()=>busy(cleanDialog.querySelector('[data-clean-submit]'),async()=>{const result=await api(`/api/materials/${cleanId}/cleanup`,{method:'POST'});cleanDialog.close();await refresh();toast(`已清理 ${result.deleted} 项，跳过 ${result.skipped.length} 项`);}).catch(e=>toast(e.message,true));
const renderJobsBefore22=renderJobs;
renderJobs=function(){renderJobsBefore22();for(const article of document.querySelectorAll('#jobs .job')){const id=article.querySelector('[data-id]')?.dataset.id,j=jobs.find(x=>x.id===id);if(!j)continue;const actions=article.querySelector('.job-actions');if(j.superseded_by||j.materials_deleted){const note=document.createElement('small');note.textContent=j.superseded_by?'旧素材已被重新生成替换':'历史素材已清理';actions.append(note);if(j.rollback_directory&&!j.materials_deleted){const clean=document.createElement('button');clean.className='text-btn';clean.textContent='清理替换前的旧版备份';clean.onclick=()=>previewCleanup(id).catch(e=>toast(e.message,true));actions.append(clean);}continue;}if(!activeStatus(j.status)&&['batch','batch-video','analysis','material-merge'].includes(j.kind)){const clean=document.createElement('button');clean.className='text-btn';clean.textContent='清理历史素材';clean.onclick=()=>previewCleanup(id).catch(e=>toast(e.message,true));actions.append(clean);}}};
