#!/usr/bin/env node
import {connect,submitBatch} from '../integrations/desk_client.mjs';
const args=process.argv.slice(2), positional=[],opts={};
for(let i=0;i<args.length;i++){
  const s=args[i];if(s.startsWith('--')){const key=s.slice(2).replaceAll('-','_');opts[key]=args[i+1]&&!args[i+1].startsWith('--')?args[++i]:true;}else positional.push(s);
}
const command=positional.shift()??'help';
if(command==='help'){
  console.log(`映格 Video Desk 2.5.1
video-desk storyboard <链接或本机视频> [--mode direct|cache] [--start 0] [--duration 60] [--transcript auto|off|subtitles] [--output 完整路径]
video-desk probe|subtitles <视频> [同上]
video-desk zoom <任务ID> --start 10 --duration 8
video-desk locate <任务ID> <台词>
video-desk export <任务ID> [--frames] [--audio] [--video] [--output 完整路径]
video-desk batch <多个完整路径> [--folder 文件夹] [--outputs frames,audio,puzzles,transcript,clips,ai,zip] [--segment 10] [--dedup exact|4|8|12|0..16]
video-desk jobs | status <任务ID> | cancel <任务ID> | resume <任务ID>
video-desk model-install [--path 离线模型包] | model-export [--output 完整路径]
共用选项：--url http://127.0.0.1:端口；--home 应用目录；--no-wait 只创建任务。
EXE 用法：VideoDesk.exe --cli storyboard <视频>，首次自动准备并启动后台。`);
  process.exit(0);
}
try{
  const client=await connect({url:opts.url,home:opts.home});let result;
  if(command==='batch'){
    let inputs=positional;
    if(opts.paths_file){const {readFile}=await import('node:fs/promises');inputs=await readFile(opts.paths_file,'utf8');}
    const selected=opts.outputs?String(opts.outputs).split(','):undefined;
    result=await submitBatch(client,{inputs,folder:opts.folder,recursive:!opts.no_recursive,outputs:selected,kind:opts.kind??'rules',output:opts.output,segment_seconds:Number(opts.segment??0),
      frames:{mode:opts.frame_mode??'seconds',value:Number(opts.frame_value??3),start:Number(opts.start??0),duration:Number(opts.duration??0),limit:Number(opts.limit??2000)},
      analysis:{transcript:opts.transcript??'auto',transcript_file:opts.transcript_file??'',start:Number(opts.start??0),duration:Number(opts.duration??0),max_cells_per_sheet:Number(opts.max_cells_per_sheet??0)},
      ai_package:{enabled:!opts.legacy_ai,combined:!!opts.combined,screen_text:!opts.no_screen_text},
      dedup:{enabled:opts.dedup!==undefined,mode:opts.dedup==='exact'?'exact':'similar',threshold:opts.dedup==='exact'?0:Number(opts.dedup??4)},request_id:opts.request_id});
  }else if(['storyboard','probe','subtitles'].includes(command)){
    if(!positional[0])throw new Error('缺少视频链接或完整路径');
    const body={input:positional[0],kind:command==='storyboard'?'board':command};
    for(const k of ['output','mode','strategy','pick','transcript','transcript_file','language','cookie_id','source_job','detail','hotwords'])if(opts[k]!==undefined)body[k]=opts[k];
    for(const k of ['start','duration','max_frames','max_sheets','max_cells_per_sheet','batch_seconds','max_height','scan_gap','integral_seconds','strip_step','strip_count'])if(opts[k]!==undefined)body[k]=Number(opts[k]);
    body.ai_package={enabled:!opts.legacy_ai,screen_text:!opts.no_screen_text};
    for(const k of ['ocr','audio'])if(opts[k])body[k]=true;
    if(opts.no_frames)body.keep_frames=false;if(opts.no_login)body.login=false;
    if(opts.band)body.band=String(opts.band).split(',').map(Number);
    if(opts.terms)body.terms=JSON.parse(opts.terms);if(opts.tier_map)body.tier_map=JSON.parse(opts.tier_map);
    result=await client.api('/api/analysis',body);
  }else if(command==='jobs')result=(await client.api('/api/state')).jobs;
  else if(command==='status')result=await client.api(`/api/analysis/${positional[0]}`);
  else if(command==='locate')result=await client.api(`/api/analysis/${positional.shift()}/locate`,{query:positional.join(' ')});
  else if(command==='zoom')result=await client.api(`/api/analysis/${positional[0]}/zoom`,opts.cell?{cell:Number(opts.cell)}:{start:Number(opts.start??0),duration:Number(opts.duration??10)});
  else if(command==='export'&&opts.outputs)result=await client.api(`/api/exports/${positional[0]}`,{request_id:opts.request_id??crypto.randomUUID(),output:opts.output??'',outputs:Object.fromEntries(['frames','audio','puzzles','transcript','clips','ai','zip'].map(k=>[k,String(opts.outputs).split(',').includes(k)])),segments:{enabled:!!opts.segment,seconds:Number(opts.segment??10),organize:true}});
  else if(command==='export')result=await client.api(`/api/analysis/${positional[0]}/export`,{request_id:opts.request_id??crypto.randomUUID(),output:opts.output??'',frames:!!opts.frames,audio:!!opts.audio,video:!!opts.video});
  else if(['cancel','resume'].includes(command))result=await client.api(`/api/jobs/${positional[0]}/${command}`,{});
  else if(command==='model-install')result=await client.api('/api/models/install',{id:'sensevoice',path:opts.path??null});
  else if(command==='model-export')result=await client.api('/api/models/export',{id:'sensevoice',output:opts.output??''});
  else throw new Error('未知命令。运行 help 查看用法');
  if(result?.id&&result?.status&&!opts.no_wait){
    const controller=new AbortController();process.once('SIGINT',()=>controller.abort());
    result=await client.wait(result,{signal:controller.signal,progress:j=>console.error(`[${j.id}] ${j.status} ${j.message}`)});
  }
  console.log(JSON.stringify(result,null,2));
}catch(e){console.error(e.message);process.exitCode=1;}
