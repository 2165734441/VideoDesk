// Shared loopback client for the CLI and DSH adapter. No account data is copied.
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
export async function connect({url=process.env.VIDEO_DESK_URL,home=process.env.VIDEO_DESK_HOME}={}) {
  if(!url){
    home??=join(process.env.LOCALAPPDATA??process.env.HOME,'VideoDesk');
    try{url=JSON.parse(await readFile(join(home,'data/server.json'),'utf8')).url;}
    catch{throw new Error('请先启动映格 EXE，或用 --url http://127.0.0.1:端口指定网页服务。');}
  }
  const parsed=new URL(url);
  if(parsed.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname)||parsed.username||parsed.password)throw new Error('客户端只连接本机映格服务');
  const base=parsed.origin;
  const response=await fetch(base+'/api/state',{signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw new Error('无法读取本机服务状态');
  const state=await response.json();
  if(state.application!=='video-desk')throw new Error('目标不是映格服务');
  async function api(path,body){
    const r=await fetch(base+path,{signal:AbortSignal.timeout(body===undefined?5000:15000),method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-App-Token':state.token},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${r.status}`);return data;
  }
  async function wait(job,{progress=()=>{},signal}={}){
    let stamp='';
    while(['queued','running','waiting','processing','recording','waiting_source'].includes(job.status)){
      if(signal?.aborted){await api(`/api/jobs/${job.id}/cancel`,{});throw new Error('已取消');}
      if(job.message!==stamp){progress(job);stamp=job.message;}
      await new Promise(r=>setTimeout(r,400));
      job=(await api('/api/state')).jobs.find(j=>j.id===job.id);
      if(!job)throw new Error('任务已不存在');
    }
    if(job.status!=='done')throw new Error(`${job.status}: ${job.message} (job=${job.id})`);
    return job;
  }
  return {api,wait,state,base};
}

// Shared by CLI and DSH; path splitting/quoting is authoritative on the server.
export async function submitBatch(client,args={}) {
  let sources=[];
  if(args.folder){const result=await client.api('/api/sources/discover',{folder:args.folder,recursive:args.recursive!==false});sources.push(...result.sources);}
  const inputs=args.inputs??args.paths??args.path_lines;
  if(inputs?.length){const result=await client.api('/api/sources/register',{paths:inputs});sources.push(...result.sources);}
  const ids=[...new Set([...(args.source_ids??[]),...sources.filter(s=>s.status!=='invalid').map(s=>s.id)])];
  if(!ids.length)throw new Error('没有可处理的本机视频：'+sources.map(s=>s.error).filter(Boolean).join('；'));
  const selected=args.outputs??['puzzles','transcript','ai'];
  const outputs=Object.fromEntries(['frames','audio','puzzles','transcript','clips','ai','zip'].map(k=>[k,selected.includes(k)]));
  const segment=Number(args.segment_seconds??0);
  return client.api('/api/batches',{sources:ids,request_id:args.request_id??crypto.randomUUID(),output:args.output??'',kind:args.kind??'board',outputs,
    frames:args.frames??{mode:'seconds',value:3},analysis:args.analysis??{},ai_package:args.ai_package??{enabled:outputs.ai,combined:!!args.combined},replace_job_id:args.replace_job_id??'',
    segments:{enabled:segment>0||outputs.clips,seconds:segment||10,organize:args.organize!==false},
    dedup:args.dedup??{enabled:false,mode:'exact',threshold:4}});
}
