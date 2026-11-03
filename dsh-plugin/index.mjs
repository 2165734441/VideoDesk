import {connect,submitBatch} from './desk_client.mjs';
import {pathToFileURL} from 'node:url';
export const name='dsh-video-desk';
export const inject=['tools'];
const text=(description,required=false)=>({type:'string',description,...(required?{required:true}:{})});
const number=description=>({type:'number',description});
const common={input:text('视频网址或本机视频完整路径',true),mode:text('cache 缓存分析；direct 仅抽图不写入视频'),start:number('原视频起始秒'),duration:number('处理秒数，0 表示到结尾'),strategy:text('full / standard / compact / skim / brief'),transcript:text('auto / subtitles / off'),transcript_file:text('可选 SRT/VTT/JSON 完整路径'),cookie_id:text('设置中已导入的 Cookie 标识'),max_frames:number('每段最多分镜格数'),max_sheets:number('每段最多拼图数'),max_cells_per_sheet:number('每张拼图最多画面数，0 自动；超出另起一张，保留已选画面'),ai_package:{type:'json',description:'enabled/screen_text/scan_seconds/batch_files；集中上传完整台词与编号画面'},ocr:{type:'boolean',description:'硬字幕 OCR 抽检校正'},audio:{type:'boolean',description:'导出音轨'}};
function attachments(ctx){try{return typeof ctx.get==='function'?ctx.get('attachments'):ctx.attachments;}catch{return null;}}
export async function execute(ctx,kind,args){
  const client=await connect();
  if(kind==='batch'||kind==='export'){const raw=kind==='batch'?await submitBatch(client,args):await client.api(`/api/exports/${args.job_id}`,{...args,request_id:args.request_id??crypto.randomUUID()});const job=await client.wait(raw);return {text:JSON.stringify(job,null,2),images:[]};}
  if(kind==='locate')return {text:JSON.stringify(await client.api(`/api/analysis/${args.job_id}/locate`,{query:args.query}),null,2),images:[]};
  const raw=kind==='zoom'?await client.api(`/api/analysis/${args.job_id}/zoom`,args.cell!==undefined?{cell:args.cell}:{start:args.start??0,duration:args.duration??10})
    :await client.api('/api/analysis',{...args,kind:kind==='storyboard'?'board':kind});
  const job=await client.wait(raw);
  const result=await client.api(`/api/analysis/${job.id}`);
  const images=[],service=attachments(ctx);
  if(job.ai_package?.files?.length){
    let output=`job_id=${job.id}\n${job.message}\n必须一起上传的文件夹：${job.directory}/一起上传给AI\n`;
    for(const name of job.ai_package.files){
      const path='一起上传给AI/'+name,url=client.base+`/api/jobs/${job.id}/files/`+path.split('/').map(encodeURIComponent).join('/');
      const response=await fetch(url);if(!response.ok)throw new Error('AI 阅读包缺少必需文件：'+name);
      if(/\.(png|jpg)$/i.test(name)){
        if(service?.saveImage)images.push(await service.saveImage({data:Buffer.from(await response.arrayBuffer()),mediaType:name.endsWith('.jpg')?'image/jpeg':'image/png',name}));
        else output+=`未附图片：${name}；必须补充上传。\n`;
      }else output+=`\n[${name}]\n`+await response.text();
    }
    return {text:output,images};
  }
  const paths=(result.evidence?.sheets??[]).map(s=>s.file);
  if(kind==='subtitles')paths.push('subtitle-strip.jpg');
  const fileUrl=p=>client.base+`/api/jobs/${job.id}/files/`+p.split('/').map(encodeURIComponent).join('/');
  if(service?.saveImage){
    for(const path of paths.slice(0,24)){
      const r=await fetch(fileUrl(path));if(!r.ok)throw new Error('无法读取分镜图片');
      images.push(await service.saveImage({data:Buffer.from(await r.arrayBuffer()),mediaType:path.endsWith('.jpg')?'image/jpeg':'image/png',name:path.split('/').pop()}));
    }
  }
  let output=`job_id=${job.id}\n${job.message}\n保存目录：${job.directory}\n网页结果：${client.base}\n`;
  if(!service?.saveImage&&paths.length)output+='宿主未提供图片附件服务，请在映格网页或保存目录查看图片。\n';
  if(paths.length>24)output+='本次仅提交前 24 张图片，完整结果保存在上述目录。\n';
  if(kind==='probe'){
    const r=await fetch(fileUrl('probe.json'));output+=await r.text();
  }else{
    for(const s of result.evidence?.sheets??[]){
      const path=s.file.replace(/storyboard-\d+\.png$/,'分镜与台词清单.txt');
      if(output.includes(`\n[${path}]`))continue;
      const r=await fetch(fileUrl(path));if(r.ok)output+=`\n[${path}]\n`+await r.text();
      if(output.length>22000){output=output.slice(0,22000)+'\n清单超过单次展示长度，完整清单见保存目录。';break;}
    }
  }
  return {text:output,images};
}
export async function apply(ctx){
  let defineTool;
  if(process.env.DSH_TOOLS_PATH)({defineTool}=await import(pathToFileURL(process.env.DSH_TOOLS_PATH).href));
  else ({defineTool}=await import('@deepseek-ai/dsh-tools'));
  const definitions=[
    ['batch','批量导入本机视频，独立输出、去重和时间分段；不复制原片。',{inputs:{type:'array',items:{type:'string'},description:'完整视频路径列表；每项空格、逗号保留'},path_lines:text('每行一个完整路径，可带外围双引号'),folder:text('可选视频文件夹（默认含子文件夹）'),recursive:{type:'boolean',description:'是否包含子文件夹'},outputs:{type:'array',items:{type:'string'},description:'frames/audio/puzzles/transcript/clips/ai/zip；默认 AI 包'},segment_seconds:number('分段秒数，0 关闭，默认 10 秒'),output:text('保存目录'),kind:text('rules / board / subtitles'),frames:{type:'json',description:'抽帧方式 mode/value/start/duration/limit'},analysis:{type:'json',description:'共用分镜与台词参数'},dedup:{type:'json',description:'enabled/mode exact或similar/threshold 0..16'},request_id:text('可选请求标识，断线重试时复用')}],
    ['export','按需导出已有任务所选类型，与网页共用处理逻辑。',{job_id:text('任务ID',true),outputs:{type:'json',description:'frames/audio/puzzles/transcript/clips/ai/zip 布尔选项'},segments:{type:'json',description:'enabled/seconds/organize'},dedup:{type:'json',description:'enabled/mode/threshold'},output:text('保存目录'),request_id:text('可选请求标识')}],
    ['storyboard','生成编号分镜与台词，共用映格任务、登录态和输出。',common],
    ['zoom','细看已有任务的一格，或指定原视频时间范围。',{job_id:text('映格任务ID',true),cell:number('全局分镜格编号'),start:number('原视频起始秒'),duration:number('处理秒数')}],
    ['probe','读取视频时长、显示尺寸、音轨与字幕轨。',common],
    ['locate','按已生成的字幕或转写匹配台词，返回原视频时间。',{job_id:text('映格任务ID',true),query:text('原句或关键词',true)}],
    ['subtitles','生成手动或自动字幕带长图与时间索引。',{...common,strip_step:number('截取字幕间隔秒'),strip_count:number('最多字幕条数'),band:{type:'array',items:{type:'number'},description:'可选上下边界 0–1，例如 [0.75,1]'}}],
  ];
  const disposers=[];
  try{
    for(const [kind,description,parameters] of definitions)disposers.push(ctx.tools.register(defineTool({name:'video_desk_'+kind,description,parameters,
      output:{schema:{type:'object',additionalProperties:false,properties:{text:{type:'string',required:true},images:{type:'array',items:{type:'json'},required:true}}},render:(_args,value)=>[...value.images.map(attachment=>({type:'image',attachment})),{type:'text',text:value.text}]},
      timeoutMs:7200000,isConcurrencySafe:()=>true,execute:args=>execute(ctx,kind,args)})));
  }catch(e){for(const dispose of disposers.reverse())dispose?.();throw e;}
  return ()=>{for(const dispose of disposers.reverse())dispose?.();};
}
