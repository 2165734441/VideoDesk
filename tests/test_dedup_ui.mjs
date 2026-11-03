import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

function workspace() {
  const elements=new Map(),intervals=[],requests=[];
  function element(id) {
    if(elements.has(id))return elements.get(id);
    const classes=new Set(),listeners=new Map();
    const el={value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,style:{},dataset:{},
      firstElementChild:{style:{}},getContext:()=>({}),setAttribute(){},removeAttribute(){},
      classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on){on?classes.add(x):classes.delete(x);},contains:x=>classes.has(x)},
      addEventListener(name,fn){listeners.set(name,fn);},querySelector:()=>element(id+'-child'),
      async click(){await listeners.get('click')?.({target:el});},pause(){}};
    elements.set(id,el);return el;
  }
  const job={id:'missing',kind:'dedup',status:'done',message:'扫描已完成',updated:1,dedup_ready:true,title:'历史去重'};
  const state={application:'video-desk',token:'test-token',settings:{output:'C:/test-only'},jobs:[job]};
  let reportStatus=404,stateFailure=false;
  const report={summary:{images:2,groups:0,duplicates:0},groups:[],items:[],operations:[],skipped:[]};
  const context=vm.createContext({console,Map,Set,AbortController,document:{getElementById:element,createElement:element,
      querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){},scrollTo(){}},
    setTimeout:()=>0,clearTimeout(){},setInterval:fn=>intervals.push(fn),
    fetch:async url=>{requests.push(url);if(url==='/api/state'&&stateFailure)throw new TypeError('Failed to fetch');
      const status=url==='/api/state'?200:reportStatus;
      return {ok:status===200,status,json:async()=>url==='/api/state'?state:status===200?report:{detail:'去重报告尚未生成'}};}});
  for(const file of ['app.js','dedup.js'])vm.runInContext(readFileSync(new URL('../static/'+file,import.meta.url),'utf8'),context);
  const settle=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};
  return {element,requests,intervals,job,setStatus:v=>reportStatus=v,disconnect:()=>stateFailure=true,
    run:code=>vm.runInContext(code,context),settle};
}

test('missing historical report stays local to dedup; reload recovers without stale actions',async()=>{
  const page=workspace();await page.settle();
  assert.equal(page.requests.filter(x=>x.startsWith('/api/dedup/')).length,0);
  await page.run("openDedup('missing')");await page.settle();
  assert.match(page.element('dedupStatus').textContent,/历史记录.*报告不可用/);
  assert.equal(page.element('connectionText').textContent,'本地服务已连接');
  assert.equal(page.element('connectionBadge').classList.contains('disconnected'),false);
  assert.equal(page.element('dedupActions').hidden,true);
  assert.equal(page.element('dedupQuarantine').disabled,true);
  const count=page.requests.length;
  await page.run('refresh()');
  assert.equal(page.requests.length,count+1,'polling only refreshes state after a missing report');
  page.setStatus(200);await page.element('dedupReload').click();await page.settle();
  assert.equal(page.element('dedupActions').hidden,false);
  assert.equal(page.element('dedupExport').disabled,false);
  assert.match(page.element('dedupStatus').textContent,/扫描已完成/);
  page.job.updated=2;page.setStatus(404);await page.run('refresh()');
  assert.equal(page.element('dedupActions').hidden,true);
  assert.equal(page.element('dedupExport').disabled,true);
});

test('report errors do not disconnect the service; state transport failures still do',async()=>{
  const page=workspace();await page.settle();page.setStatus(500);
  await page.run("currentPage='dedup';refresh()");
  assert.match(page.element('dedupStatus').textContent,/去重结果读取失败/);
  assert.equal(page.element('connectionBadge').classList.contains('disconnected'),false);
  page.disconnect();await page.intervals[0]();
  assert.equal(page.element('connectionBadge').classList.contains('disconnected'),true);
});
