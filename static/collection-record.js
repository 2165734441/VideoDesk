/* Whole-collection download: resolve a multi-part / playlist URL first, show
   every episode, then hand the selected episode URLs to the ordinary download
   queue.  Each episode becomes its own library job, so it can later be opened
   in "已有素材" and turned into an AI package without re-downloading. */
(() => {
  const anchor = document.getElementById('downloadBtn');
  if (!anchor || document.getElementById('collectionRecord')) return;
  const section = anchor.closest('section');
  if (!section) return;

  const box = document.createElement('div');
  box.id = 'collectionRecord';
  box.className = 'collection-record';
  box.innerHTML = `
    <div class="collection-record-head">
      <div><strong>整个合集 / 多 P 一起录</strong>
        <p class="mini-note">先读取目录，确认分集后再加入下载；每个视频单独保存在素材库，之后可从“已有素材”直接生成 AI 包。</p></div>
      <label class="checkbox-line"><input id="downloadCollectionMode" type="checkbox"> 开启合集模式</label>
    </div>
    <div id="downloadCollectionBody" hidden>
      <div class="button-row">
        <button id="downloadCollectionRead" class="subtle" type="button">读取合集目录</button>
        <span id="downloadCollectionSummary" class="mini-note">粘贴 B 站多 P 链接或播放列表链接，再读取目录。</span>
      </div>
      <div id="downloadCollectionList" class="collection-record-list"></div>
      <div class="button-row">
        <button id="downloadCollectionStart" class="primary" type="button" disabled>录制选中的全部视频</button>
        <button id="downloadCollectionClear" class="text-btn" type="button">清空目录</button>
      </div>
      <p class="mini-note">合集录制只保存原视频（不勾选“直接抽帧”）；队列会按顺序逐个下载。B 站公开合集默认免登录，避免过期 Cookie 让部分分 P 下载失败；需要登录才能看的视频请改用单个下载。</p>
    </div>`;
  anchor.insertAdjacentElement('afterend', box);

  const mode = document.getElementById('downloadCollectionMode');
  const body = document.getElementById('downloadCollectionBody');
  const readButton = document.getElementById('downloadCollectionRead');
  const startButton = document.getElementById('downloadCollectionStart');
  const clearButton = document.getElementById('downloadCollectionClear');
  const summary = document.getElementById('downloadCollectionSummary');
  const list = document.getElementById('downloadCollectionList');
  let groups = [];
  let resolving = false;

  function linksFrom(value) {
    const found = String(value || '').match(/https?:\/\/[^\s"'<>]+/g) || [];
    return [...new Set(found.map(x => x.replace(/[),.;]+$/, '')))];
  }
  function clock(seconds) {
    const n = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), s = n % 60;
    return h ? `${h} 小时 ${m} 分 ${s} 秒` : m ? `${m} 分 ${s} 秒` : `${s} 秒`;
  }
  function selected() {
    return [...list.querySelectorAll('input[data-collection-url]:checked')].map(x => ({
      url: x.dataset.collectionUrl, title: x.dataset.collectionTitle || ''
    }));
  }
  function updateStart() {
    const count = selected().length;
    startButton.disabled = !count || resolving;
    startButton.textContent = count ? `录制选中的 ${count} 个视频` : '录制选中的全部视频';
  }
  function render() {
    list.replaceChildren();
    let total = 0, count = 0;
    for (const group of groups) {
      if (groups.length > 1) {
        const heading = document.createElement('p');
        heading.className = 'collection-record-group';
        heading.textContent = `${group.title} · ${group.items.length} 个视频`;
        list.append(heading);
      }
      for (const item of group.items) {
        total += Number(item.duration || 0); count += 1;
        const row = document.createElement('label');
        row.className = 'collection-record-row';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.checked = true;
        checkbox.dataset.collectionUrl = item.url;
        checkbox.dataset.collectionTitle = item.title || '';
        checkbox.addEventListener('change', updateStart);
        const index = document.createElement('span');
        index.className = 'collection-record-index';
        index.textContent = String(item.index).padStart(2, '0');
        const text = document.createElement('span');
        text.className = 'collection-record-name';
        text.textContent = item.title || `第 ${item.index} 个视频`;
        const time = document.createElement('span');
        time.className = 'collection-record-time';
        time.textContent = clock(item.duration);
        row.append(checkbox, index, text, time);
        list.append(row);
      }
    }
    if (!count) {
      list.textContent = '没有读到分集。';
      summary.textContent = '没有读到分集。';
      updateStart();
      return;
    }
    summary.textContent = `共 ${count} 个视频，约 ${clock(total)}；勾选要录的，默认全选。`;
    updateStart();
  }

  mode.addEventListener('change', () => {
    body.hidden = !mode.checked;
    if (mode.checked) body.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  readButton.addEventListener('click', () => busy(readButton, async () => {
    const links = linksFrom(document.getElementById('downloadUrls').value);
    if (!links.length) throw new Error('请先在上方粘贴合集或多 P 视频链接');
    resolving = true; updateStart();
    summary.textContent = '正在读取合集目录，长合集需要十几秒…';
    try {
      const results = [];
      for (const link of links) {
        results.push(await api('/api/collections/resolve', {
          method: 'POST', timeoutMs: 120000,
          body: { url: link, login: document.getElementById('useLogin').checked,
                  cookie_id: document.getElementById('downloadCookie')?.value || '' }
        }));
      }
      const seen = new Set(); groups = [];
      for (const result of results) {
        const items = [];
        for (const item of result.items || []) {
          if (!item.url || seen.has(item.url)) continue;
          seen.add(item.url); items.push(item);
        }
        if (items.length) groups.push({ title: result.title || '合集', items });
      }
      render();
      if (!groups.length) throw new Error('没有读到可分集下载的视频');
      toast(`已读取 ${groups.reduce((n, g) => n + g.items.length, 0)} 个视频，确认后加入下载队列`);
    } finally {
      resolving = false; updateStart();
    }
  }).catch(error => { summary.textContent = error.message; toast(error.message, true); }));

  clearButton.addEventListener('click', () => {
    groups = []; list.replaceChildren(); summary.textContent = '已清空目录。';
    updateStart();
  });
  startButton.addEventListener('click', () => busy(startButton, async () => {
    const picked = selected();
    if (!picked.length) throw new Error('请至少勾选一个视频');
    const result = await api('/api/download', {
      method: 'POST', timeoutMs: 60000,
      body: {
        urls: picked.map(x => x.url),
        output: document.getElementById('output').value,
        // B 站公开视频用免登录通道更稳；登录 Cookie 过期时 1080P
        // 的签名地址会让个别分 P 失败，已实测。其他平台仍沿用页面开关。
        login: picked.every(x => /bilibili\.com/i.test(x.url)) ? false : document.getElementById('useLogin').checked,
        cookie_id: document.getElementById('downloadCookie')?.value || '',
        extract: document.getElementById('downloadExtract').checked,
        frames_only: false,
        max_height: 0,
        frames: { mode: document.getElementById('downloadFrameMode').value,
                  value: Number(document.getElementById('downloadFrameValue').value) || 3, audio: true }
      }
    });
    toast(`已把 ${result.ids.length} 个视频加入下载队列；完成后可在素材库逐个生成 AI 包`);
    await refresh(); goto('library');
  }).catch(error => toast(error.message, true)));
})();
