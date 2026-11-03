// Runs vendored storyboard algorithms. The private request arrives on stdin.
import cp from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {dirname, resolve, join} from 'node:path';
import {pathToFileURL} from 'node:url';

// All FFmpeg, ffprobe and OCR children are invisible and bounded by the
// controller's owned process tree. Preserve execFile's promisify signature.
for (const name of ['spawn', 'execFile']) {
  const original = cp[name];
  const wrapped = function(file, args, options, callback) {
    if (typeof options === 'function') {callback = options; options = {};}
    const opts = {...(options ?? {}), windowsHide:true};
    if (/(?:^|[\\/])ffmpeg[^\\/]*?(?:\.exe)?$/i.test(String(file))) {
      const threads=String(Math.max(1,Math.min(8,Number(process.env.VIDEO_DESK_DECODE_THREADS)||2)));
      args = ['-threads',threads,'-filter_threads','1','-filter_complex_threads','1',...(args??[])];
      if(args.includes('-i')&&args.length>1)args=[...args.slice(0,-1),'-threads',threads,args.at(-1)];
    }
    return name === 'spawn' ? original.call(cp, file, args, opts)
      : original.call(cp, file, args, opts, callback);
  };
  if (name === 'execFile') {
    const custom = Symbol.for('nodejs.util.promisify.custom');
    wrapped[custom] = (file,args,options) => new Promise((res,rej) => {
      wrapped(file,args,options,(error,stdout,stderr) => error ? rej(Object.assign(error,{stdout,stderr})) : res({stdout,stderr}));
    });
  }
  cp[name] = wrapped;
}
syncBuiltinESMExports();
const emit = value => process.stdout.write(JSON.stringify(value)+'\n');
let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const request = JSON.parse(input);
  process.env.VIDEO_DESK_TRANSCRIPT_SOURCE = request.transcriptSource ?? '';
  // fileURLToPath handles spaces and non-ASCII Windows installation paths.
  const {fileURLToPath} = await import('node:url');
  const actualRoot = resolve(dirname(fileURLToPath(import.meta.url)),'../third_party/dsh-video-storyboard/src/storyboard');
  const mod = name => import(pathToFileURL(join(actualRoot,name+'.js')).href);
  const {probeVideo} = await mod('probe');
  if (request.command === 'parse-transcript') {
    const {loadTranscriptFile} = await mod('transcript-file');
    emit({result:await loadTranscriptFile(request.path)});
  } else if (request.command === 'locate') {
    const {matchSegments} = await mod('locate');
    emit({result:matchSegments(request.segments,request.query,{max:request.max??20})});
  } else if (request.command === 'probe') {
    emit({result:await probeVideo(request.input)});
  } else if (request.command === 'subtitles') {
    const {buildSubtitleStrip} = await mod('subtitle-strip');
    emit({result:await buildSubtitleStrip({...request.options,input:request.input,out:request.out})});
  } else {
    const {runStoryboard} = await mod('pipeline');
    const {boardOptions} = await mod('board');
    const {buildEvidence,formatManifest} = await mod('format');
    let last = 0;
    const options = {...boardOptions({...request.options,input:request.input},{kind:request.kind??'board'}),
      ...(request.audioInput?{audioInput:request.audioInput}:{}),
      cache:!request.direct, sourceHash:false, frameConcurrency:1,
      onProgress:p=> {if(Date.now()-last>500){last=Date.now();emit({progress:p});}}};
    if(request.options.transcript === 'off') {options.transcriptFile=undefined;}
    const result = await runStoryboard(options);
    result.info.path = request.publicSource;
    const evidence = buildEvidence(result);
    evidence.engine = 'dsh-video-storyboard';
    evidence.upstream_commit = request.commit;
    evidence.video_saved = !request.direct && request.cached === true;
    evidence.window = result.window;
    evidence.plan = result.plan;
    evidence.audio = result.audio;
    evidence.transcript = result.transcript;
    evidence.ocr = result.ocr ?? null;
    evidence.timing = result.timing;
    evidence.sheets = [];
    await mkdir(request.out,{recursive:true});
    for (const sheet of result.sheets) {
      const file = `storyboard-${String(sheet.sheet).padStart(3,'0')}.png`;
      await writeFile(join(request.out,file),sheet.png);
      evidence.sheets.push({file,width:sheet.width,height:sheet.height,cells:sheet.cells.map(c=>c.i)});
    }
    let manifest = formatManifest(result,{maxCellChars:({low:60,med:120,high:400})[request.options.detail]??120,maxChars:10000000});
    // The upstream estimate is specific to DeepSeek; do not present it as
    // a fixed price/token count for every AI service.
    manifest = manifest.split('\n').filter(line=>!/^cost:/.test(line)).join('\n')
      .replace(/ tokens="[^"]*"/g,'').replace(/~?\d+ vision tokens[^\n]*/g,'视觉消耗取决于所用模型');
    manifest = '视觉 token 数由实际使用的 AI 模型决定；本包不提供通用固定计费估算。\n'+manifest;
    await writeFile(join(request.out,'分镜与台词清单.txt'),manifest,'utf8');
    await writeFile(join(request.out,'evidence.json'),JSON.stringify(evidence,null,2),'utf8');
    emit({result:evidence});
  }
} catch(error) {
  // No request headers or resolved CDN links are written to stdout/logs.
  emit({error:String(error?.message??error).replace(/https?:\/\/\S+/g,'[媒体来源]').slice(0,1200)});
  process.exitCode = 1;
}
