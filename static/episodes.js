'use strict';
// Online links share the serial batch pipeline and the same export options.
function episodePrefs(){
  workflowSettings.board.online_episodes??={};
  workflowSettings.board.online_episodes.mode??='separate';workflowSettings.board.online_episodes.items??=[];
  return workflowSettings.board.online_episodes;
}
let episodePending=null;
try{episodePending=JSON.parse(localStorage.getItem('yingge-episode-pending')||'null');}catch{}
const episodePanel=document.createElement('details');episodePanel.className='advanced';episodePanel.id='onlineEpisodes';episodePanel.open=true;
episodePanel.innerHTML='<summary>多链接分集与合并</summary><p class="mini-note">上方每行粘贴一条链接，点“加入分集列表”。同一集内可放多条视频；输出用集号、集标题和视频标题区分。</p><button type="button" class="subtle" data-episode-action="add">加入分集列表</button><label class="field">分集方式<select id="episodeLayout"><option value="separate">每条链接单独一集</option><option value="one">全部链接合成一集</option><option value="custom">按列表里的集号分组</option></select></label><label class="checkbox-line"><input type="checkbox" id="episodeCombined">另外导出全部集数的联合 AI 分析包</label><div data-episode-list></div><button type="button" class="text-btn" data-episode-action="clear">清空分集列表</button><p class="mini-note">合并包保留完整台词、必要图片、原片时间和每集分界。单集内容仍分文件夹保存；不会拼接新的视频文件。</p>';
$('analysisOnline').append(episodePanel);
optionHelp.episodeLayout='每条链接一集：按列表顺序编号。全部合成一集：多条视频放进同一集的 AI 包。按集号分组：相同集号的视频合成一集，可修改标题、顺序或移除。原视频不修改。';
optionHelp.episodeCombined='勾选后额外生成全部集数的联合 AI 包，带集号、标题、原片时间和联合时间；不勾选则仅按集导出。两种情况下，各集的文件夹与分界均保留。';
attachOptionHelp(episodePanel);
function effectiveEpisodes(){
  const p=episodePrefs();return p.items.map((item,i)=>({...item,episode:p.mode==='one'?1:p.mode==='separate'?i+1:Number(item.episode)||i+1,
    episode_title:p.mode==='one'?(p.items[0]?.episode_title||''):(item.episode_title||'')}));
}
function renderEpisodes(){
  const p=episodePrefs(),items=effectiveEpisodes();$('episodeLayout').value=p.mode;$('episodeCombined').checked=!!workflowSettings.board.ai_package?.combined;
  episodePanel.querySelector('[data-episode-list]').innerHTML=items.map((s,i)=>`<article class="source-row"><div><strong>第 ${String(s.episode).padStart(3,'0')} 集 · 视频 ${i+1}</strong><small>${html(s.input)}</small><div class="inline-fields"><label class="field">集号<input type="number" min="1" max="10000" data-episode-number="${i}" value="${s.episode}" ${p.mode!=='custom'?'disabled':''}></label><label class="field">本集标题（可选）<input data-episode-title="${i}" value="${html(s.episode_title)}" placeholder="留空使用集号"></label></div></div><div class="button-row"><button type="button" class="text-btn" data-episode-action="up" data-index="${i}" ${i?'':'disabled'} aria-label="上移链接">↑</button><button type="button" class="text-btn" data-episode-action="down" data-index="${i}" ${i===items.length-1?'disabled':''} aria-label="下移链接">↓</button><button type="button" class="text-btn" data-episode-action="remove" data-index="${i}">移除</button></div></article>`).join('');
}
function readOnlineLinks(value){
  const lines=value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  return lines.map(s=>{s=s.replace(/^["“](.*)["”]$/,'$1');const m=s.match(/^\[[^\]]*\]\((https?:\/\/[^\s]+)\)$/);if(m)s=m[1];let url;try{url=new URL(s);}catch{throw new Error('每行请只填写一条完整视频链接。');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error('请使用有效的 HTTP / HTTPS 视频链接。');return url.href;});
}
function addEpisodeLinks(){
  const p=episodePrefs(),links=readOnlineLinks($('analysisInput').value);
  if(!links.length)throw new Error('先在上方粘贴视频链接，每行一条。');
  let next=Math.max(0,...p.items.map(s=>Number(s.episode)||0));
  for(const input of links){if(p.items.some(s=>s.input===input))continue;p.items.push({input,episode:++next,episode_title:''});}
  if(p.items.length>1000)throw new Error('每批最多 1000 条链接，请分批处理。');
  $('analysisInput').value='';saveLocalState();renderEpisodes();toast(`分集列表共 ${p.items.length} 条链接`);
}
episodePanel.addEventListener('click',e=>{
  const b=e.target.closest('[data-episode-action]');if(!b)return;
  try{const p=episodePrefs(),i=Number(b.dataset.index),action=b.dataset.episodeAction;
    if(action==='add'){addEpisodeLinks();return;}
    if(action==='clear')p.items=[];
    if(action==='remove')p.items.splice(i,1);
    if(action==='up'&&i>0)[p.items[i-1],p.items[i]]=[p.items[i],p.items[i-1]];
    if(action==='down'&&i<p.items.length-1)[p.items[i+1],p.items[i]]=[p.items[i],p.items[i+1]];
    saveLocalState();renderEpisodes();
  }catch(error){toast(error.message,true);}
});
episodePanel.addEventListener('change',e=>{
  const p=episodePrefs(),el=e.target;
  if(el.id==='episodeLayout'){const before=effectiveEpisodes();p.mode=el.value;if(p.mode==='custom')p.items=before;}
  else if(el.id==='episodeCombined'){workflowSettings.board.ai_package.combined=el.checked;syncExportPanels();}
  else if(el.dataset.episodeNumber!==undefined){const i=Number(el.dataset.episodeNumber),n=Number(el.value);if(!Number.isInteger(n)||n<1||n>10000){toast('集号请输入 1–10000 的整数',true);renderEpisodes();return;}p.items[i].episode=n;const same=p.items.find((s,k)=>k!==i&&s.episode===n);if(same)p.items[i].episode_title=same.episode_title;}
  else if(el.dataset.episodeTitle!==undefined){const i=Number(el.dataset.episodeTitle),items=effectiveEpisodes(),number=items[i].episode;for(let k=0;k<p.items.length;k++)if(items[k].episode===number)p.items[k].episode_title=el.value.slice(0,120);}
  saveLocalState();renderEpisodes();
});
const syncBeforeEpisodes=syncExportPanels;
syncExportPanels=function(){syncBeforeEpisodes();renderEpisodes();};
async function submitOnlineEpisodes(kind,analysis){
  if($('analysisInput').value.trim())addEpisodeLinks();
  const sources=effectiveEpisodes();if(!sources.length)throw new Error('请先添加视频链接。');
  activateWorkflow('board');readExportSettings($('analysisExportChoices'));
  if(!workflowSettings.board.outputs.ai||!workflowSettings.board.ai_package.enabled)throw new Error('分集 AI 导出请勾选“附 AI 分析索引”和“生成集中上传的 AI 阅读包”。');
  const outputs={...workflowSettings.board.outputs};
  const body={sources:['online'],online_sources:sources,kind,workflow:kind,performance:workflowSettings.board.performance,output:$('output').value,analysis,outputs,
    dedup:workflowSettings.board.dedup,segments:workflowSettings.board.segments,frames:frameSettings(),
    ai_package:{...workflowSettings.board.ai_package}};
  const signature=JSON.stringify(body);
  if(!episodePending||episodePending.signature!==signature)episodePending={signature,id:crypto.randomUUID()};
  localStorage.setItem('yingge-episode-pending',JSON.stringify(episodePending));
  const job=await api('/api/batches',{method:'POST',body:{...body,request_id:episodePending.id}});
  episodePending=null;localStorage.removeItem('yingge-episode-pending');lastBatch=job.id;saveLocalState();
  $('analysisImmediate').textContent='分集任务已建立，按集处理并显示进度。';await refresh();goto('library');toast('分集导出已加入队列');
}
renderEpisodes();
