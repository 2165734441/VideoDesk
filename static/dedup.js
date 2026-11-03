'use strict';
let dedupId = '', dedupReport = null, dedupSignature = '', dedupPage = 0, dedupLoading = false;
let dedupReportError = '';
const dedupChoices = new Map(), dedupEnabled = new Set(), dedupExpanded = new Map();

function clearDedupReport() {
  dedupReport=null;dedupPage=0;
  dedupChoices.clear();dedupEnabled.clear();dedupExpanded.clear();
  $('dedupGroups').innerHTML='';$('dedupSummary').hidden=true;$('dedupActions').hidden=true;
  $('dedupSkipped').hidden=true;$('dedupPager').hidden=true;
  $('dedupExport').disabled=true;$('dedupQuarantine').disabled=true;$('dedupRestore').disabled=true;
}

function dedupOptions(select, options) {
  const value = select.value;
  const markup = options.map(([id, label])=>`<option value="${html(id)}">${html(label)}</option>`).join('');
  if (select.innerHTML !== markup) {select.innerHTML = markup; select.value = value;}
}

async function refreshDedup() {
  dedupOptions($('dedupSourceJob'), [['', '使用上面填写的文件夹'], ...jobs.filter(j=>j.kind!=='dedup'&&!activeStatus(j.status)).map(j=>[j.id,j.title])]);
  const history = jobs.filter(j=>j.kind==='dedup');
  dedupOptions($('dedupHistory'), history.length?history.map(j=>[j.id,`${j.title} · ${statusLabels[j.status]||j.status}`]):[['','尚无去重记录']]);
  if (!dedupId && history.length) dedupId = history[0].id;
  $('dedupHistory').value = dedupId;
  const job = history.find(j=>j.id===dedupId);
  if (!job) return;
  $('dedupStatus').textContent = dedupReportError || `${statusLabels[job.status]||job.status} · ${job.message}`;
  const active = activeStatus(job.status);
  if ($('dedupReload')) $('dedupReload').disabled = active || dedupLoading || !job.dedup_ready;
  $('dedupProgress').hidden = !active;
  $('dedupProgress').firstElementChild.style.width = `${Math.max(2,Math.min(100,job.progress||0))}%`;
  const quarantined = dedupReport?.operations.some(o=>o.action==='quarantine'&&o.state!=='restored');
  $('dedupExport').disabled = active || !dedupReport || quarantined;
  $('dedupQuarantine').disabled = active || !dedupReport || quarantined || !dedupEnabled.size;
  $('dedupRestore').hidden = !quarantined;
  $('dedupRestore').disabled = active;
  $('dedupGroups').classList.toggle('dedup-locked',active);
  const signature = job.id + ':' + job.updated;
  if (currentPage!=='dedup' || active || !job.dedup_ready || signature===dedupSignature || dedupLoading) return;
  dedupLoading = true;
  const requestedId = dedupId;
  try {
    const report = await api(`/api/dedup/${requestedId}`);
    if (requestedId!==dedupId) return;
    dedupReport = report; dedupSignature = signature; dedupPage = 0;
    dedupReportError='';$('dedupStatus').textContent=`${statusLabels[job.status]||job.status} · ${job.message}`;
    dedupChoices.clear(); dedupEnabled.clear(); dedupExpanded.clear();
    report.groups.forEach(g=>{dedupChoices.set(g.id,g.keep);dedupEnabled.add(g.id);});
    renderDedup();
  } catch (error) {
    if (requestedId!==dedupId) return;
    clearDedupReport();
    if (error.status===404) {
      dedupSignature=signature;
      dedupReportError='这条历史记录的去重报告不可用，文件可能已移动、删除或保存位置暂时无法访问。恢复报告目录后点击“重新读取报告”，也可选择来源重新扫描。';
      $('dedupStatus').textContent=dedupReportError;
      return;
    }
    throw error;
  } finally {dedupLoading=false;if ($('dedupReload')) $('dedupReload').disabled=active||!job.dedup_ready;}
}

function renderDedup() {
  const report = dedupReport;
  if (!report) return;
  const summary = report.summary;
  $('dedupSummary').hidden = false;
  $('dedupSummary').innerHTML = `<div><span>有效图片</span><strong>${summary.images}</strong></div><div><span>重复分组</span><strong>${summary.groups}</strong></div><div><span>重复候选</span><strong>${summary.duplicates}</strong></div>`;
  $('dedupActions').hidden = false;
  const quarantined = report.operations.some(o=>o.action==='quarantine'&&o.state!=='restored');
  $('dedupRestore').hidden = !quarantined;
  $('dedupExport').disabled = quarantined || !summary.images;
  $('dedupQuarantine').disabled = quarantined || !dedupEnabled.size;
  $('dedupOperations').innerHTML = report.operations.slice().reverse().map(o=>`<p class="dedup-operation"><strong>${o.action==='export'?'去重副本':o.state==='restored'?'隔离图片已恢复':'可恢复隔离区'}${o.state==='partial'?' · 部分完成，请查看记录':''}</strong><br>${html(o.directory)}</p>`).join('') + (report.truncated?'<div class="notice">已达到扫描上限；未扫描的文件不会导出或处理。请提高上限或分批扫描。</div>':'');
  const items = new Map(report.items.map(i=>[i.id,i]));
  const pageSize = 12, groups = report.groups.slice(dedupPage*pageSize,(dedupPage+1)*pageSize);
  $('dedupGroups').innerHTML = groups.length?groups.map((g,index)=>{
    const shown = dedupExpanded.get(g.id)||24, keep = dedupChoices.get(g.id);
    return `<article class="dedup-group"><div class="dedup-group-head"><label class="checkbox-line"><input type="checkbox" data-dedup-group="${g.id}" ${dedupEnabled.has(g.id)?'checked':''}>处理第 ${dedupPage*pageSize+index+1} 组 <span class="badge">${g.kind==='exact'?'完全重复':'相似候选'}</span></label><span class="muted">${g.members.length} 张 · 保留 1 张</span></div><div class="dedup-grid">${g.members.slice(0,shown).map(iid=>{
      const item = items.get(iid);
      return `<label class="dedup-image ${keep===iid?'is-keeper':''}"><a href="/api/dedup/${dedupId}/image/${iid}" target="_blank" rel="noopener" title="打开原图核对细节"><img loading="lazy" src="/api/dedup/${dedupId}/thumbnail/${iid}" alt="${html(item.relative)}"></a><span class="dedup-image-choice"><input type="radio" name="keep-${g.id}" data-dedup-keep="${g.id}" value="${iid}" ${keep===iid?'checked':''}>保留这张${iid===g.keep?' · 推荐':''}</span><strong title="${html(item.relative)}">${html(item.relative)}</strong><small>${item.width} × ${item.height} · ${(item.size/1024).toFixed(1)} KB${item.animated?' · 动画':''}</small></label>`;
    }).join('')}</div>${g.members.length>shown?`<button class="text-btn" data-dedup-more="${g.id}">继续显示本组图片（剩余 ${g.members.length-shown} 张）</button>`:''}</article>`;
  }).join(''):'<div class="jobs-empty"><strong>没有发现重复图片。</strong>你仍可导出本次扫描的全部有效图片。</div>';
  const pages = Math.ceil(report.groups.length/pageSize);
  $('dedupPager').hidden = pages<=1;
  $('dedupPageLabel').textContent = `第 ${dedupPage+1} / ${pages} 页`;
  $('dedupPrev').disabled = dedupPage===0; $('dedupNext').disabled = dedupPage>=pages-1;
  $('dedupSkipped').hidden = !report.skipped.length;
  $('dedupSkipped').querySelector('summary').textContent = `无法处理的图片（${report.skipped.length} 项，最多记录 500 项）`;
  $('dedupSkipped').querySelector('pre').textContent = report.skipped.map(i=>i.file+'：'+i.reason).join('\n');
}

async function openDedup(id) {
  if (dedupId!==id) {
    dedupId=id;dedupSignature='';dedupReportError='';clearDedupReport();
  }
  goto('dedup');await refreshDedup();
}
bind('dedupPick',()=>busy($('dedupPick'),async()=>{
  const result=await api('/api/pick-directory',{method:'POST'});
  if(result.path){$('dedupSource').value=result.path;$('dedupSourceJob').value='';$('dedupSource').disabled=false;}
}));
$('dedupSourceJob').addEventListener('change',()=>{$('dedupSource').disabled=!!$('dedupSourceJob').value;});
$('dedupMode').addEventListener('change',()=>{$('dedupThreshold').disabled=$('dedupMode').value!=='similar';});
bind('dedupScan',()=>busy($('dedupScan'),async()=>{
  const result=await api('/api/dedup',{method:'POST',body:{source:$('dedupSource').value,source_job:$('dedupSourceJob').value,
    output:$('output').value,mode:$('dedupMode').value,threshold:Number($('dedupThreshold').value),
    recursive:$('dedupRecursive').checked,limit:Number($('dedupLimit').value)}});
  await refresh();await openDedup(result.id);toast('图片扫描已加入队列');
}));
$('dedupHistory').addEventListener('change',()=>openDedup($('dedupHistory').value).catch(e=>toast(e.message,true)));
if ($('dedupReload')) bind('dedupReload',async()=>{dedupSignature='';dedupReportError='';clearDedupReport();await refreshDedup();});
$('dedupGroups').addEventListener('change',event=>{
  const el=event.target;
  if(el.dataset.dedupGroup){if(el.checked)dedupEnabled.add(el.dataset.dedupGroup);else dedupEnabled.delete(el.dataset.dedupGroup);}
  if(el.dataset.dedupKeep)dedupChoices.set(el.dataset.dedupKeep,el.value);
  renderDedup();
});
$('dedupGroups').addEventListener('click',event=>{
  const el=event.target.closest('[data-dedup-more]');if(!el)return;
  const id=el.dataset.dedupMore;dedupExpanded.set(id,(dedupExpanded.get(id)||24)+48);renderDedup();
});
bind('dedupPrev',()=>{dedupPage--;renderDedup();$('dedupGroups').scrollIntoView({block:'start'});});
bind('dedupNext',()=>{dedupPage++;renderDedup();$('dedupGroups').scrollIntoView({block:'start'});});
async function dedupAction(action, button) {
  await busy(button,async()=>{
    const choices=Object.fromEntries([...dedupEnabled].map(g=>[g,dedupChoices.get(g)]));
    await api(`/api/dedup/${dedupId}/action`,{method:'POST',body:{action,choices}});
    await refresh();toast(action==='restore'?'正在恢复隔离图片':action==='export'?'正在导出去重副本，原图保留':'正在将重复项移入隔离区，可通过“恢复隔离图片”撤销');
  });
}
bind('dedupExport',()=>dedupAction('export',$('dedupExport')));
bind('dedupQuarantine',()=>dedupAction('quarantine',$('dedupQuarantine')));
bind('dedupRestore',()=>dedupAction('restore',$('dedupRestore')));
bind('dedupOpen',()=>api(`/api/jobs/${dedupId}/open-folder`,{method:'POST'}));
