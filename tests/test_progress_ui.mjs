import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../static/app.js',import.meta.url),'utf8').split('const $ =')[0];
const context=vm.createContext({html:value=>String(value).replace(/[<>]/g,'')});
vm.runInContext(source,context);
const render=job=>context.jobProgress(job);
test('download progress shows real bytes, speed and stage estimate',()=>{
  const result=render({status:'running',progress:12,progress_detail:{downloaded_bytes:1048576,total_bytes:2097152,speed:524288,eta:2}});
  assert.match(result,/总进度约 12%/);assert.match(result,/1.0 MB \/ 2.0 MB/);assert.match(result,/512 KB\/秒/);
  assert.match(result,/aria-valuenow="12"/);
});
test('unknown duration is animated without a fake percentage; waiting is paused',()=>{
  const unknown=render({status:'running',progress:2,progress_indeterminate:true});
  assert.match(unknown,/is-indeterminate/);assert.doesNotMatch(unknown,/aria-valuenow/);
  const waiting=render({status:'waiting',progress:2});assert.match(waiting,/等待网站验证/);assert.doesNotMatch(waiting,/is-indeterminate/);
  assert.match(render({status:'waiting_source',progress:2}),/等待来源.*重新选择并继续/);
  assert.match(render({status:'done',progress:99}),/已完成 100%/);
});
