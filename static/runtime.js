'use strict';
(() => {
  const banner = document.createElement('section');
  banner.className = 'notice runtime-warning'; banner.hidden = true;
  banner.setAttribute('role', 'status');
  document.querySelector('.topbar').after(banner);
  const card = document.createElement('section');
  card.className = 'panel padded runtime-card';
  card.innerHTML = '<h2>运行组件与修复</h2><p class="mini-note">修复会重建程序组件并重新打开工作台，保留账号、设置和素材。正在进行的任务需先完成或取消。</p><div id="runtimeComponents">正在检查…</div><div class="button-row"><button id="runtimeRepair" class="subtle">检测并修复</button><button id="runtimeRefresh" class="text-btn">刷新组件状态</button></div><p id="runtimeRepairNote" class="mini-note" role="status"></p>';
  $('page-settings').append(card);
  let last = '';
  function render(runtime) {
    if (!runtime) return;
    const signature = JSON.stringify(runtime);
    if (signature === last) return;
    last = signature;
    const failed = (runtime.components || []).filter(c => !c.ready);
    banner.hidden = !failed.length;
    banner.innerHTML = `<strong>部分功能暂不可用：</strong>${failed.map(c=>html(c.name)).join('、')}。其他功能可继续使用。<button class="text-btn" id="runtimeShowSettings">查看原因与修复</button>`;
    banner.querySelector('button').onclick = () => {goto('settings'); card.scrollIntoView({behavior:'smooth',block:'center'});};
    $('runtimeComponents').innerHTML = (runtime.components || []).map(c => `<p><strong>${html(c.name)}</strong> · ${c.ready?'已就绪':html(c.message||'暂不可用')}</p>`).join('') || '<p class="mini-note">当前由源码启动；组件在使用时检查。可使用新版 EXE 获取自动检测与修复。</p>';
    $('runtimeRepair').disabled = !runtime.managed;
    const failedOcr=failed.some(c=>c.id==='ocr'),failedAsr=failed.some(c=>c.id==='asr');
    if(failedOcr || failedAsr) $('runtimeRepairNote').textContent='识别未完成的内容会在资料包中明确标注；请修复后重新生成，或提供现成字幕。';
  }
  bind('runtimeRefresh',()=>busy($('runtimeRefresh'),async()=>render(await api('/api/runtime-status'))));
  bind('runtimeRepair',()=>busy($('runtimeRepair'),async()=>{
    const result=await api('/api/runtime-repair',{method:'POST'});
    $('runtimeRepairNote').textContent=result.message;toast(result.message);
    $('runtimeRepair').dataset.requested='1';
  }));
  window.addEventListener('video-desk-state',event=>render(event.detail.runtime));
  api('/api/runtime-status').then(render).catch(e=>{$('runtimeComponents').textContent=e.message;});
})();
