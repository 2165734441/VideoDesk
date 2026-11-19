import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=readFileSync(new URL('../mobile/assets/live-resolver.js',import.meta.url),'utf8');
const [prefix,suffix]=source.split('__EXPECTED__');
assert.ok(prefix&&suffix,'Test the resolver asset shipped in the Android APK');
const rid='246801143607',roomId='7707856210269047594',sec='MS4wLjAB12345';
const wanted='https://live.douyin.com/'+rid;
const redirected='https://webcast.amemv.com/douyin/webcast/reflow/'+roomId;
const room=(status=2)=>({idStr:roomId,status,owner:{webRid:rid,secUid:sec,nickname:'测试主播',status:0}});
const rsc=value=>'self.__rsc_f.push('+JSON.stringify([1,'5:'+JSON.stringify(value)+'\n'])+')';
function detect({url=wanted,payload={},scripts=[],text='',videos=[],links=[],expected=wanted}={}){
  const document={querySelectorAll:s=>s==='video'?videos:s.startsWith('a[')?links:[{textContent:JSON.stringify(payload)},...scripts.map(textContent=>({textContent}))],body:{innerText:text}};
  return JSON.parse(vm.runInNewContext(prefix+JSON.stringify(expected)+suffix,{URL,window:{},document,location:new URL(url)}, {timeout:200}));
}

test('mobile redirect page matches its requested web room through owner.webRid',()=>{
  assert.equal(detect({url:redirected,scripts:[rsc(['$',{data:{room:room()}}])]}).state,'online');
});
test('ended room status wins over recommendation badges and playing video',()=>{
  assert.equal(detect({url:redirected,scripts:[rsc({room:room(4)})],text:'直播已结束 推荐直播中',videos:[{readyState:3,paused:false,ended:false,videoWidth:720}]}).state,'offline');
});
test('recommended room and account-status fields cannot identify the requested room',()=>{
  assert.equal(detect({url:redirected,scripts:[rsc({room:{...room(),owner:{...room().owner,webRid:'999999'}}})]}).state,'unknown');
  assert.equal(detect({url:redirected,scripts:[rsc({user:room().owner})]}).state,'unknown');
});
test('profile links match exact owner.secUid and never nickname',()=>{
  assert.equal(detect({expected:'https://www.douyin.com/user/'+sec,url:redirected,scripts:[rsc({room:room()})]}).state,'online');
  assert.equal(detect({expected:'https://www.douyin.com/user/MS4wLjABother',url:redirected,scripts:[rsc({room:room()})]}).state,'unknown');
});
test('reflow share link matches the full string ID without numeric precision loss',()=>{
  assert.equal(detect({expected:redirected,url:redirected,scripts:[rsc({room:room()})]}).state,'online');
  assert.equal(detect({expected:redirected+'1',url:redirected,scripts:[rsc({room:room()})]}).state,'unknown');
});
test('JSON split across consecutive React stream chunks is reassembled',()=>{
  const line='5:'+JSON.stringify({room:room()})+'\n';const cut=37;
  const scripts=[line.slice(0,cut),line.slice(cut)].map(c=>'self.__rsc_f.push('+JSON.stringify([1,c])+');');
  assert.equal(detect({url:redirected,scripts}).state,'online');
});
test('raw text records before room JSON do not hide the room record',()=>{
  const scripts=['self.__rsc_f.push('+JSON.stringify([1,'8:T4,raw!'])+')',rsc({room:room()})];
  assert.equal(detect({url:redirected,scripts}).state,'online');
});
test('script text is parsed as data and unsupported records stay unknown',()=>{
  assert.equal(detect({scripts:['self.__rsc_f.push((()=>{throw Error("must never execute")})())']}).state,'unknown');
  assert.equal(detect({scripts:[rsc({room:room('invalid')})]}).state,'unknown');
});
test('legacy JSON, encoded JSON, profile offline status and percent titles remain supported',()=>{
  assert.equal(detect({payload:{room:{web_rid:rid,status:2,title:'100%直播',owner:{nickname:'主播'}}}}).state,'online');
  assert.equal(detect({scripts:[encodeURIComponent(JSON.stringify({room:room()}))]}).state,'online');
  assert.equal(detect({expected:'https://www.douyin.com/user/'+sec,payload:{user:{sec_uid:sec,live_status:0}}}).state,'offline');
});
test('DOM fallback requires exact page identity and playing video',()=>{
  const videos=[{readyState:3,paused:false,ended:false,videoWidth:720}];
  assert.equal(detect({text:'直播中',videos}).state,'online');
  assert.equal(detect({url:'https://live.douyin.com/999999',text:'直播中',videos}).state,'unknown');
  assert.equal(detect({url:redirected,text:'直播中',videos}).state,'unknown');
  assert.equal(detect({text:'直播中',videos:[{...videos[0],paused:true}]}).state,'unknown');
});

test('native opening receives the exact string room ID, not the public web ID',()=>{
  const result=detect({url:redirected,scripts:[rsc({room:room()})]});
  assert.equal(result.room_id,roomId);
  assert.notEqual(result.room_id,rid);
  assert.equal(detect({url:redirected,scripts:[rsc({room:room(4)})]}).room_id,undefined);
});
test('an unsafe numeric room ID is never passed to the native app',()=>{
  const value=room();delete value.idStr;value.id=Number(roomId);
  assert.equal(detect({payload:{room:value}}).state,'online');
  assert.equal(detect({payload:{room:value}}).room_id,undefined);
  value.id=123456;
  assert.equal(detect({payload:{room:value}}).room_id,'123456');
});
test('profile summary never hides the matched room ID or ended state',()=>{
  const expected='https://www.douyin.com/user/'+sec;
  const profile={secUid:sec,live_status:1,nickname:'测试主播'};
  assert.equal(detect({expected,payload:{user:profile,room:room()}}).room_id,roomId);
  assert.equal(detect({expected,payload:{user:profile,room:room(4)}}).state,'offline');
});
test('short links use exact room identity from their final redirect',()=>{
  const expected='https://v.douyin.com/testShare/';
  assert.equal(detect({expected,url:redirected,scripts:[rsc({room:room()})]}).room_id,roomId);
  assert.equal(detect({expected,url:wanted,payload:{room:room()}}).room_id,roomId);
  assert.equal(detect({expected,url:redirected+'1',payload:{room:room()}}).state,'unknown');
  assert.equal(detect({expected,url:'https://www.douyin.com/video/123456',payload:{room:room()}}).state,'unknown');
});
test('missing, null and unrecognized room status are not treated as offline',()=>{
  for(const state of [null,'',false,0,1,3,NaN])assert.equal(detect({payload:{room:room(state)}}).state,'unknown');
});

test('profile room_data locates nested owner.web_rid only for the requested user',()=>{
  const expected='https://www.douyin.com/user/'+sec;
  const payload={user:{sec_uid:sec,live_status:1,room_data:JSON.stringify({status:2,owner:{web_rid:rid}})}};
  assert.equal(detect({expected,url:expected,payload}).room_url,wanted);
  assert.equal(detect({expected:expected+'other',url:expected,payload}).state,'unknown');
});

test('exact profile live avatar yields full room ID when JSON does not expose profile response',()=>{
  const expected='https://www.douyin.com/user/'+sec;
  const link={href:wanted+'?enter_method=web_homepage_head&room_id='+roomId,innerText:'直播中'};
  assert.equal(detect({expected,url:expected,links:[link]}).room_id,roomId);
  assert.equal(detect({expected,url:expected+'other',links:[link]}).state,'unknown');
  assert.equal(detect({expected,url:expected,links:[{...link,href:wanted+'?enter_method=recommend&room_id='+roomId}]}).state,'unknown');
  assert.equal(detect({expected,url:expected,links:[link,link]}).state,'unknown');
  assert.equal(detect({expected,url:expected,links:[link],payload:{user:{sec_uid:sec,live_status:0}}}).state,'offline');
});

test('stream URL comes only from the exact requested room',()=>{
 const own=room();own.streamUrl={flvPullUrl:{SD1:'https://pull.douyincdn.com/own.flv'}};
 const other={...room(),owner:{webRid:'9999999999'},streamUrl:{flvPullUrl:{SD1:'https://pull.douyincdn.com/other.flv'}}};
 assert.equal(detect({payload:{other,own}}).stream_url,'https://pull.douyincdn.com/own.flv');
 assert.equal(detect({payload:{other}}).stream_url,undefined);
});
test('SDK stream JSON resolves FLV while refusing HEVC variants',()=>{
 const own=room();own.streamUrl={liveCoreSdkData:{pullData:{streamData:JSON.stringify({data:{sd:{main:{flv:'https://pull.douyincdn.com/live.flv',hls:'https://pull.douyincdn.com/live.m3u8'}},h265:{main:{flv:'https://pull.douyincdn.com/hevc.flv'}}}})}}};
 assert.equal(detect({payload:{own}}).stream_url,'https://pull.douyincdn.com/live.flv');
});
test('ended room can never provide a recording URL',()=>{const own=room(4);own.streamUrl={flvPullUrl:{SD1:'https://pull.douyincdn.com/live.flv'}};assert.equal(detect({payload:{own}}).stream_url,undefined);});
