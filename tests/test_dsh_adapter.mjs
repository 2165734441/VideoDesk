import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execute} from '../dsh-plugin/index.mjs';
import {connect} from '../integrations/desk_client.mjs';
// Integration tests must explicitly opt in to an isolated service and fixture.
const integrationReady=Boolean(process.env.VIDEO_DESK_URL&&process.env.VIDEO_DESK_TEST_VIDEO&&process.env.VIDEO_DESK_TEST_OUTPUT);
test('DSH adapter creates the same API job and returns committed PNG attachments',{skip:!integrationReady},async()=>{
  const data=[];
  const value=await execute({get:name=>name==='attachments'?{saveImage:async request=>{
    assert.equal(request.mediaType,'image/png');assert.equal(request.data.subarray(1,4).toString(),'PNG');data.push(request.data.length);return {test_attachment:data.length};
  }}:null},'storyboard',{input:process.env.VIDEO_DESK_TEST_VIDEO,transcript:'off',duration:2,keep_frames:false});
  assert.match(value.text,/job_id=/);assert.ok(data.length>0);assert.equal(value.images.length,data.length);
  const client=await connect(),id=value.text.match(/job_id=([a-f0-9]+)/)[1];
  const request={request_id:crypto.randomUUID(),frames:false,audio:false,video:false,output:process.env.VIDEO_DESK_TEST_OUTPUT??''};
  const first=await client.api(`/api/analysis/${id}/export`,request);
  const repeated=await client.api(`/api/analysis/${id}/export`,request);
  assert.equal(first.id,repeated.id);
  await assert.rejects(()=>client.api(`/api/analysis/${id}/export`,{...request,frames:true}),/请求标识/);
  assert.equal((await client.wait(first)).status,'done');
});

test('DSH batch and selected export share the web pipeline without extra outputs',{skip:!integrationReady},async()=>{
  const batch=await execute({get:()=>null},'batch',{inputs:[process.env.VIDEO_DESK_TEST_VIDEO],outputs:['audio'],output:process.env.VIDEO_DESK_TEST_OUTPUT??''});
  const parent=JSON.parse(batch.text);assert.equal(parent.kind,'batch');assert.equal(parent.status,'done');assert.ok(parent.files.length>0);assert.ok(parent.files.every(f=>f.name.endsWith('.m4a')));
  const exported=await execute({get:()=>null},'export',{job_id:parent.id,outputs:{frames:true},output:process.env.VIDEO_DESK_TEST_OUTPUT??''});
  const next=JSON.parse(exported.text);assert.equal(next.status,'done');assert.ok(next.files.length>0);assert.ok(next.files.every(f=>f.name.endsWith('.jpg')));
});
