(()=>{
 const wanted=__EXPECTED__, quality=(__QUALITY__||'standard');
 let want,page;
 try{want=new URL(wanted);page=new URL(location.href);}catch(e){return JSON.stringify({state:'unknown'});}
 const liveId=u=>u.hostname==='live.douyin.com'?(u.pathname.match(/^\/(\d{4,30})\/?$/)?.[1]||''):'';
 const reflowId=u=>u.hostname==='webcast.amemv.com'?(u.pathname.match(/^\/douyin\/webcast\/reflow\/(\d{4,30})\/?$/)?.[1]||''):'';
 const sec=['www.douyin.com','douyin.com'].includes(want.hostname)?(want.pathname.match(/^\/user\/([A-Za-z0-9_-]{8,200})\/?$/)?.[1]||''):'';
 const short=want.hostname==='v.douyin.com';
 const rid=liveId(want)||(short?liveId(page):'');
 const reflow=reflowId(want)||(short?reflowId(page):'');
 const id=v=>typeof v==='string'&&/^\d{4,30}$/.test(v)?v:typeof v==='number'&&Number.isSafeInteger(v)&&v>=1000?String(v):'';
 const status=v=>(typeof v==='number'||typeof v==='string'&&/^\d+$/.test(v))&&Number.isFinite(Number(v))?Number(v):null;
 const pick=map=>{
   const order={
     origin:['origin','uhd','full_hd1','full_hd','original','hd1','hd','sd1','sd2','standard','ld1','ld'],
     high:['hd1','hd','full_hd1','origin','sd1','sd2','standard','ld1','ld'],
     standard:['sd1','sd2','standard','sd','hd1','hd','origin','ld1','ld'],
     low:['sd2','sd1','ld1','ld','standard','sd','hd1','hd','origin']
   }[quality]||['sd1','sd2','standard','sd','hd1','hd','origin'];
   for(const key of order)if(map[key])return map[key];
   const keys=Object.keys(map);return keys.length?map[keys[0]]:'';
 };
 const streamMaps=room=>{
   let stream=room.stream_url||room.streamUrl||{};
   if(typeof stream==='string')try{stream=JSON.parse(stream);}catch(e){stream={};}
   const flv={},hls={};
   const put=(map,key,value,kind)=>{
     if(typeof value!=='string'||!/^https?:\/\//.test(value))return;
     if(/h265|hevc/i.test(key)||/(?:codec|vcodec)=h?265/i.test(value))return;
     const k=String(key||'').toLowerCase();
     if(kind==='hls'){if(/\.m3u8(?:[?]|$)/i.test(value)||/m3u8/i.test(value))map[k]=value;}
     else if(/\.flv(?:[?]|$)/i.test(value)||/flv/i.test(value))map[k]=value;
   };
   for(const name of ['flv_pull_url','flvPullUrl'])
     if(stream[name]&&typeof stream[name]==='object')for(const [k,v] of Object.entries(stream[name]))put(flv,k,v,'flv');
   for(const name of ['hls_pull_url_map','hlsPullUrlMap'])
     if(stream[name]&&typeof stream[name]==='object')for(const [k,v] of Object.entries(stream[name]))put(hls,k,v,'hls');
   const core=stream.live_core_sdk_data||stream.liveCoreSdkData||{};
   const pull=core.pull_data||core.pullData||{};
   let raw=pull.stream_data||pull.streamData;
   if(typeof raw==='string')try{raw=JSON.parse(raw);}catch(e){raw=null;}
   if(raw&&typeof raw==='object'&&raw.data)for(const [k,v] of Object.entries(raw.data)){
     const main=(v&&(v.main||v))||{};
     if(main.flv)put(flv,k,main.flv,'flv');
     if(main.hls)put(hls,k,main.hls,'hls');
   }
   const seen=new WeakSet();
   const scan=(v,depth=0)=>{
     if(!v||typeof v!=='object'||depth>9||seen.has(v))return;seen.add(v);
     for(const [k,x] of Object.entries(v)){
       if(typeof x==='string'){
         if(/^https?:\/\//.test(x)&&!/h265|hevc/i.test(k)){
           const key=String(k).toLowerCase();
           if((/\.flv(?:[?]|$)/i.test(x)||/flv/i.test(x))&&!flv[key])flv[key]=x;
           if((/\.m3u8(?:[?]|$)/i.test(x)||/m3u8/i.test(x))&&!hls[key])hls[key]=x;
         }
       }else scan(x,depth+1);
     }
   };
   scan(stream);
   return {flv,hls,flv_url:pick(flv),hls_url:pick(hls)};
 };
 let answer=null,profile=null;
 const seen=new WeakSet();
 const visit=v=>{
   if(!v||typeof v!=='object'||answer||seen.has(v))return;seen.add(v);
   if(sec&&String(v.sec_uid||v.secUid||'')===sec){
     if(status(v.live_status)!==null)profile={state:status(v.live_status)>0?'online':'offline',name:v.nickname||''};
     let rd=v.room_data||v.roomData;
     try{if(typeof rd==='string')rd=JSON.parse(rd);}catch(e){rd=null;}
     if(rd&&typeof rd==='object'){
       const ro=rd.owner||{};
       const pr=id(rd.web_rid)||id(rd.webRid)||id(ro.web_rid)||id(ro.webRid)||id(v.web_rid)||id(v.webRid);
       if(pr&&status(rd.status)===2)profile={state:'online',name:v.nickname||'',room_url:'https://live.douyin.com/'+pr};
     }
     const owner=v.owner||{};
     const roomRid=id(v.web_rid)||id(v.webRid)||id(owner.web_rid)||id(owner.webRid);
     const roomId=id(v.id_str)||id(v.idStr)||id(v.room_id_str)||id(v.room_id)||id(v.id);
     const roomSec=String(owner.sec_uid||owner.secUid||'');
     const isRoom=!v.sec_uid&&!v.secUid&&(v.owner||v.id_str||v.idStr||v.room_id_str||v.web_rid);
     if(isRoom&&[2,4].includes(status(v.status))&&((rid&&roomRid===rid)||(sec&&roomSec===sec)||(reflow&&roomId===reflow))){
       answer={state:status(v.status)===2?'online':'offline',name:owner.nickname||v.nickname||''};
       if(answer.state==='online'&&roomId){
         answer.room_id=roomId;
         const sm=streamMaps(v);
         answer.stream_url=sm.flv_url;
         answer.hls_url=sm.hls_url;
       }
       return;
     }
   }
   for(const k in v)if(typeof v[k]==='object')visit(v[k]);
 };
 for(const root of [window._ROUTER_DATA,window.__INITIAL_STATE__,window.__NEXT_DATA__])visit(root);
 const chunks=[];
 for(const e of document.querySelectorAll('script')){
   const text=e.textContent;
   try{let data;try{data=JSON.parse(text);}catch(x){data=JSON.parse(decodeURIComponent(text));}visit(data);}catch(x){}
   const m=text.trim().match(/^self\.__(?:rsc_f|pace_f)\.push\((\[.*\])\);?$/s);
   if(m)try{const chunk=JSON.parse(m[1]);if(chunk[0]===1&&typeof chunk[1]==='string')chunks.push(chunk[1]);}catch(x){}
 }
 for(const line of [...chunks,chunks.join('')].flatMap(chunk=>chunk.split('\n'))){
   const colon=line.indexOf(':');
   if(colon<1||!/^[0-9a-f]+$/i.test(line.slice(0,colon)))continue;
   try{visit(JSON.parse(line.slice(colon+1)));}catch(x){}
 }
 if(!answer)answer=profile;
 // Newer live pages stream their room payload through self.__pace_f instead of
 // exposing _ROUTER_DATA. If the structured walk missed it, read the published
 // quality maps directly from the page text; a real .m3u8/.flv is enough proof.
 if(!answer){
   const raw=(document.documentElement.innerHTML||'').replace(/\\"/g,'"').replace(/\\u0026/g,'&').replace(/\\\//g,'/');
   const grab=label=>{
     const m=raw.match(new RegExp('"'+label+'":\\{([^}]*)\\}'));
     if(!m)return null;
     const out={};let x;const item=/"([^"]+)":"(https?:[^"]+)"/g;
     while((x=item.exec(m[1])))out[x[1].toLowerCase()]=x[2];
     return out;
   };
   const hm=grab('hls_pull_url_map'),fm=grab('flv_pull_url');
   const hls=hm?pick(hm):'',flv=fm?pick(fm):'';
   if(hls||flv)answer={state:'online',name:'',hls_url:hls,stream_url:flv};
 }
 if(sec&&['www.douyin.com','douyin.com'].includes(page.hostname)&&page.pathname.replace(/\/$/,'')==='/user/'+sec&&(!answer||answer.state==='online'&&!answer.room_id)){
   const links=[];
   for(const a of document.querySelectorAll('a[data-e2e="web_others_homepage"][href]')){
     try{
       const u=new URL(a.href);
       const wr=liveId(u),room=id(u.searchParams.get('room_id'));
       if(wr&&room&&u.searchParams.get('enter_method')==='web_homepage_head'&&/直播中/.test(a.innerText||''))links.push({state:'online',room_id:room,room_url:'https://live.douyin.com/'+wr});
     }catch(e){}
   }
   if(links.length===1)answer=links[0];
 }
 if(!answer&&rid&&liveId(page)===rid){
   const text=document.body.innerText;
   if(/直播已结束|暂未开播|还未开播|暂时没有开播/.test(text))answer={state:'offline'};
   else if(/直播中|正在直播/.test(text)&&[...document.querySelectorAll('video')].some(v=>v.readyState>=2&&!v.paused&&!v.ended&&v.videoWidth>0))answer={state:'online'};
 }
 return JSON.stringify(answer||{state:'unknown',message:'页面没有提供可靠开播状态，请手动确认直播或开启抖音开播通知'});
})()
