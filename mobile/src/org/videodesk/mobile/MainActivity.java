package org.videodesk.mobile;
import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.WebView;
import android.widget.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;

/**
 * Phone companion: one list, one action per anchor. Detection and scheduling
 * live on the desktop, so the phone only shows state and records on request.
 */
public class MainActivity extends Activity {
 static final String[] TARGETS={"由电脑录制","由手机录制","通知我选择"};
 static final String[] MODES={"pc","phone","ask"};
 static MainActivity current;
 final Handler handler=new Handler(Looper.getMainLooper());
 final ExecutorService worker=Executors.newSingleThreadExecutor();
 JSONArray anchors=new JSONArray();
 LinearLayout list;
 TextView status,connection,listHint,listenStatus;
 StreamResolver streams;
 boolean visible=true,polling=false,permissionPending=false,cancelPermission=false,linked=false;
 boolean independent=false;
 long nextPoll=0,nextScan=0;
 int scanCursor=0;
 String currentId="",lastSignature="";
 final Set<String> acceptedCommands=new HashSet<>();
 /** Anchor id -> last time the phone itself looked for that anchor going live. */
 final java.util.Map<String,Long> scanAt=new java.util.HashMap<>();

 @Override public void onCreate(Bundle saved){
  super.onCreate(saved);current=this;Detector.attach(this);
  getWindow().setStatusBarColor(Color.rgb(20,107,99));
  getWindow().setNavigationBarColor(Color.WHITE);
  getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
  FrameLayout host=new FrameLayout(this);host.setBackgroundColor(Color.rgb(244,246,246));setContentView(host);
  LinearLayout root=new LinearLayout(this);root.setOrientation(1);
  host.addView(root,new FrameLayout.LayoutParams(-1,-1));
  int pad=(int)(14*getResources().getDisplayMetrics().density);
  root.setOnApplyWindowInsetsListener((view,insets)->{
   int top=insets.getSystemWindowInsetTop(),bottom=insets.getSystemWindowInsetBottom();
   view.setPadding(pad,top+pad/2,pad,bottom+pad/2);return insets;});
  TextView heading=new TextView(this);heading.setText("映格手机伴侣");heading.setTextSize(20);
  heading.setTextColor(Color.rgb(20,107,99));heading.setTypeface(null,android.graphics.Typeface.BOLD);
  root.addView(heading);
  connection=new TextView(this);connection.setTextSize(13);connection.setTextColor(Color.rgb(96,102,106));
  connection.setPadding(0,pad/3,0,0);connection.setText("正在连接电脑…");root.addView(connection);
  status=new TextView(this);status.setTextSize(12);status.setTextColor(Color.rgb(126,132,136));
  status.setMaxLines(2);status.setPadding(0,pad/4,0,pad/2);root.addView(status);
  listenStatus=new TextView(this);listenStatus.setTextSize(12);listenStatus.setTextColor(Color.rgb(20,107,99));
  listenStatus.setPadding(0,0,0,pad/3);listenStatus.setVisibility(View.GONE);root.addView(listenStatus);
  ScrollView scroll=new ScrollView(this);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  list=new LinearLayout(this);list.setOrientation(1);scroll.addView(list);
  // A phone screen only fits a few cards, and a list that fills the screen
  // exactly looks finished. Say how many anchors exist and that it scrolls.
  listHint=new TextView(this);listHint.setTextSize(11);listHint.setTextColor(Color.rgb(150,156,160));
  listHint.setPadding(pad/2,pad/2,pad/2,pad/3);root.addView(listHint);
  LinearLayout bar=new LinearLayout(this);bar.setGravity(Gravity.CENTER_VERTICAL);
  barButton(bar,"＋ 添加主播",this::addAnchorDialog);
  barButton(bar,"下载视频",this::videoDialog);
  barButton(bar,"回放",this::replayDialog);
  barButton(bar,"设置",this::settingsDialog);
  root.addView(bar);
  LinearLayout playBar=new LinearLayout(this);playBar.setGravity(Gravity.CENTER_VERTICAL);
  barButton(playBar,"听直播",this::listenDialog);
  barButton(playBar,"听视频",this::listenVideoDialog);
  barButton(playBar,"停止听",()->AudioPlayer.stop());
  barButton(playBar,"录制设置",this::recordOptionsDialog);
  root.addView(playBar);
  TextView shareTip=new TextView(this);shareTip.setTextSize(11);shareTip.setTextColor(Color.rgb(20,107,99));
  shareTip.setText("下载不用复制链接：在抖音点“分享”，选择“映格手机伴侣”，会直接加入电脑下载。");
  shareTip.setPadding(0,pad/3,0,pad/3);root.addView(shareTip);
  try{anchors=new JSONArray(getSharedPreferences("desk",0).getString("anchors","[]"));}catch(Exception ignored){}
  FrameLayout pages=new FrameLayout(this);root.addView(pages,new LinearLayout.LayoutParams(-1,0));
  WebView resolverView=new WebView(this);host.addView(resolverView,0,new FrameLayout.LayoutParams(-1,-1));resolverView.setAlpha(0f);
  try{streams=new StreamResolver(this,resolverView);}catch(Exception e){status.setText("视频解析组件加载失败，请重新安装手机伴侣");}
  independent=getSharedPreferences("desk",0).getBoolean("independent",false);
  acceptIntent(getIntent());
  render();
  handler.postDelayed(tick,1200);
  worker.execute(()->VideoLibrary.recover(this));
  StreamService.sync(this);
 }

 void barButton(LinearLayout bar,String title,Runnable action){
  Button b=new Button(this);b.setText(title);b.setTextSize(13);b.setAllCaps(false);
  b.setOnClickListener(v->action.run());bar.addView(b,new LinearLayout.LayoutParams(0,-2,1));
 }

 // Telephony default button metrics make every card ~170dp tall, so only four
 // of eight anchors fit on screen and the rest looked missing. Compact them.
 void compact(Button b){
  b.setAllCaps(false);b.setTextSize(12);
  b.setMinHeight(0);b.setMinimumHeight(0);
  int v=(int)(6*getResources().getDisplayMetrics().density);b.setPadding(0,v,0,v);
 }

 // ---------- list ----------

 String signature(){
  StringBuilder text=new StringBuilder();
  for(int i=0;i<anchors.length();i++){
   JSONObject a=anchors.optJSONObject(i);
   JSONObject runtime=a.optJSONObject("runtime");
   text.append(a.optString("id")).append('|').append(a.optString("name")).append('|')
       .append(a.optBoolean("enabled")).append('|').append(runtime==null?"":runtime.optString("message")).append('|');
  }
  for(StreamService.Capture c:StreamService.captures.values())
   text.append(c.anchor.optString("id")).append('=').append(c.bytes/1048576).append(',');
  text.append(StreamService.transferMessage);
  return text.toString();
 }

 void render(){
  if(list==null)return;
  String sig=signature();if(sig.equals(lastSignature))return;lastSignature=sig;
  list.removeAllViews();
  if(anchors.length()==0){
   TextView empty=new TextView(this);empty.setText("还没有主播。点下方“添加主播”，粘贴抖音主页、直播、店铺或视频分享链接即可。");
   empty.setTextColor(Color.rgb(120,126,130));empty.setPadding(8,24,8,24);list.addView(empty);return;
  }
  int pad=(int)(14*getResources().getDisplayMetrics().density);
  if(listHint!=null)listHint.setText(anchors.length()==0?"":"共 "+anchors.length()+" 位主播"+(anchors.length()>3?" · 列表可上下滑动":""));
  for(int i=0;i<anchors.length();i++){
   JSONObject a=anchors.optJSONObject(i);if(a==null)continue;
   String id=a.optString("id");
   JSONObject runtime=a.optJSONObject("runtime");
   StreamService.Capture capture=StreamService.captures.get(id);
   LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);
   card.setPadding(pad,pad/2,pad,pad/2);
   GradientDrawable bg=new GradientDrawable();bg.setColor(Color.WHITE);bg.setCornerRadius(pad);
   card.setBackground(bg);
   LinearLayout.LayoutParams cardParams=new LinearLayout.LayoutParams(-1,-2);
   cardParams.setMargins(0,0,0,pad/2);card.setLayoutParams(cardParams);
   LinearLayout head=new LinearLayout(this);head.setGravity(Gravity.CENTER_VERTICAL);
   TextView title=new TextView(this);title.setTextSize(15);title.setTextColor(Color.rgb(32,36,38));
   title.setText(a.optString("name").isEmpty()?"待读取主播昵称":a.optString("name"));
   title.setMaxLines(1);title.setEllipsize(android.text.TextUtils.TruncateAt.END);
   head.addView(title,new LinearLayout.LayoutParams(0,-2,1));
   TextView state=new TextView(this);state.setTextSize(12);state.setPadding(0,pad/6,0,pad/6);
   String message;int color;
   if(capture!=null){
    message="手机录制中 · "+(capture.bytes/1048576)+" MB · "+Math.max(0,(System.currentTimeMillis()-capture.started)/1000)+" 秒";
    color=Color.rgb(198,40,40);
   }else if(!a.optBoolean("enabled",false)){
    message="已关闭自动录播";color=Color.rgb(140,146,150);
   }else{
    String pc=runtime==null?"":runtime.optString("message");
    String device=runtime==null?"":runtime.optString("device");
    boolean live=linked&&runtime!=null&&"recording".equals(runtime.optString("state"));
    if(!linked)pc=independent?"手机自己检测中 · 开播后自动录到手机":"电脑未连接 · 打开电脑端后自动继续";
    else if(pc.isEmpty())pc="等待电脑检测开播";
    if(live)pc="pc".equals(device)?"电脑正在录制这一场":"手机正在录制这一场";
    message=pc;color=live?Color.rgb(198,40,40):Color.rgb(120,126,130);
   }
   state.setText(message);state.setTextColor(color);state.setMaxLines(2);
   boolean pcRecording=runtime!=null&&"recording".equals(runtime.optString("state"))&&"pc".equals(runtime.optString("device"));
   Button action=new Button(this);
   if(capture!=null){action.setText("停止手机录制");action.setOnClickListener(v->{StreamService.stop(id);lastSignature="";render();});}
   else{action.setText("手机录这一路");action.setOnClickListener(v->{currentId=id;phoneRecord(a);});}
   compact(action);
   Button remote=new Button(this);
   remote.setText(pcRecording?"停止电脑录制":"电脑录这一路");
   remote.setOnClickListener(v->{currentId=id;pcRecord(a,pcRecording);});
   compact(remote);
   LinearLayout buttons=new LinearLayout(this);buttons.setOrientation(LinearLayout.HORIZONTAL);
   LinearLayout.LayoutParams buttonParams=new LinearLayout.LayoutParams(0,-2,1);
   buttons.addView(action,buttonParams);
   buttons.addView(remote,new LinearLayout.LayoutParams(0,-2,1));
   LinearLayout autoRow=new LinearLayout(this);autoRow.setGravity(Gravity.CENTER_VERTICAL);
   TextView autoLabel=new TextView(this);autoLabel.setTextSize(12);autoLabel.setTextColor(Color.rgb(96,102,106));
   autoLabel.setText(a.optBoolean("enabled",false)?"自动录播：全天检测，开播自动录":"自动录播已关闭，不会自己录");
   autoRow.addView(autoLabel,new LinearLayout.LayoutParams(0,-2,1));
   Switch auto=new Switch(this);auto.setChecked(a.optBoolean("enabled",false));
   auto.setOnCheckedChangeListener((v,checked)->{if(checked!=a.optBoolean("enabled",false))setAuto(a,checked);});
   autoRow.addView(auto);
   card.addView(head);card.addView(state);card.addView(buttons);card.addView(autoRow);
   head.setOnClickListener(v->{currentId=id;});
   list.addView(card);
  }
 }

 JSONObject selected(){
  if(currentId!=null&&!currentId.isEmpty())
   for(int i=0;i<anchors.length();i++){JSONObject a=anchors.optJSONObject(i);if(a!=null&&currentId.equals(a.optString("id")))return a;}
  return anchors.optJSONObject(0);
 }

 // ---------- add / edit ----------

 void addAnchorDialog(){addAnchorDialog("");}

 void addAnchorDialog(String preset){
  LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);
  int pad=(int)(18*getResources().getDisplayMetrics().density);box.setPadding(pad,pad/2,pad,0);
  EditText url=new EditText(this);url.setSingleLine();
  url.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
  url.setHint("主播主页 / 直播 / 店铺 / 视频分享链接");
  if(preset!=null&&!preset.trim().isEmpty())url.setText(preset.trim());
  EditText note=new EditText(this);note.setSingleLine();note.setHint("备注（可空，开播后自动读取昵称）");
  CheckBox phone=new CheckBox(this);phone.setText("用这台手机录制（推荐）");phone.setChecked(true);
  box.addView(url);box.addView(note);box.addView(phone);
  new AlertDialog.Builder(this).setTitle("添加主播")
   .setMessage("主页、直播间、店铺和已发布视频的分享链接都能加入。电脑负责检测开播，勾选后由这台手机录制并上传到该主播自己的文件夹。")
   .setView(box).setNegativeButton("取消",null)
   .setPositiveButton("保存",(d,w)->saveAnchor(url.getText().toString(),note.getText().toString(),phone.isChecked()?1:0)).show();
 }

 /** Paste/share a Douyin or Bilibili video link; the desktop owns the download. */
 void videoDialog(){
  LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);
  int pad=(int)(18*getResources().getDisplayMetrics().density);box.setPadding(pad,pad/2,pad,0);
  EditText url=new EditText(this);url.setSingleLine();url.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
  url.setHint("抖音或 B 站视频详情链接");
  String clip=clipboardText();
  if(!clip.isEmpty()){
   try{url.setText(normalizeVideo(clip));}catch(Exception ignored){}
  }
  TextView tip=new TextView(this);tip.setTextSize(12);tip.setTextColor(Color.rgb(120,126,130));
  tip.setText("最省事的方式：在抖音视频页点“分享”，选“映格手机伴侣”，不用打开本软件。\n也可以从剪贴板自动填入链接，或手动粘贴。");
  box.addView(url);box.addView(tip);
  new AlertDialog.Builder(this).setTitle("下载视频")
   .setMessage("电脑在线时加入下载队列；手机负责发起，媒体保存到电脑素材库。")
   .setView(box).setNegativeButton("取消",null)
   .setPositiveButton("下载",(d,w)->saveVideo(url.getText().toString())).show();
 }

 String clipboardText(){
  try{
   ClipboardManager cm=(ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
   if(cm!=null&&cm.hasPrimaryClip()&&cm.getPrimaryClip()!=null&&cm.getPrimaryClip().getItemCount()>0)
    return cm.getPrimaryClip().getItemAt(0).coerceToText(this).toString().trim();
  }catch(Exception ignored){}
  return "";
 }

 void saveVideo(String text){
  String url;
  try{url=normalizeVideo(text);}catch(Exception e){status.setText(e.getMessage());return;}
  JSONObject body=new JSONObject();try{body.put("url",url).put("title","手机分享视频");}catch(Exception ignored){}
  worker.execute(()->{
   String message="";
   try{
    JSONObject result=Bridge.call(this,"/v1/video",body);
    JSONArray jobs=result.optJSONArray("jobs");String id=jobs==null||jobs.length()==0?"":jobs.optString(0);
    message="已加入电脑下载"+(id.isEmpty()?"":"："+id)+"，可在电脑素材库查看";
   }catch(Exception e){message="电脑未连接或链接未通过校验："+e.getMessage();}
   final String shown=message;runOnUiThread(()->status.setText(shown));
  });
 }

 String normalizeVideo(String text)throws Exception{
  Matcher m=Pattern.compile("https?://[^\\s<>\"，。]+").matcher(text==null?"":text);
  if(!m.find())throw new Exception("请粘贴抖音或 B 站视频链接");
  String s=m.group().replaceAll("[.,;!）)]+$","");
  Uri u=Uri.parse(s);String host=(u.getHost()==null?"":u.getHost().toLowerCase()),path=u.getPath()==null?"/":u.getPath();
  if(u.getUserInfo()!=null||(u.getPort()!=-1&&u.getPort()!=443&&u.getPort()!=80))throw new Exception("链接格式不安全");
  if(Arrays.asList("v.douyin.com","www.iesdouyin.com").contains(host)){
   if(!path.matches("/[A-Za-z0-9_-]{4,80}/?"))throw new Exception("抖音分享短链不完整");
   return s;
  }
  if(Arrays.asList("www.douyin.com","douyin.com","m.douyin.com").contains(host)){
   if(!path.matches("/video/\\d{5,30}/?"))throw new Exception("请使用抖音视频详情链接");
   return s;
  }
  if("b23.tv".equals(host)){
   if(!path.matches("/[A-Za-z0-9]{4,30}/?"))throw new Exception("B 站分享短链不完整");
   return s;
  }
  if(Arrays.asList("www.bilibili.com","bilibili.com","m.bilibili.com").contains(host)){
   if(!path.matches("/video/(BV[0-9A-Za-z]{10}|av\\d{1,15})/?"))throw new Exception("请使用 B 站视频详情链接");
   return s;
  }
  throw new Exception("目前只支持抖音或 B 站视频链接");
 }

 void saveAnchor(String text,String note,int mode){
  String url;
  try{url=normalize(text);}catch(Exception e){status.setText(e.getMessage());return;}
  JSONObject body=new JSONObject();
  try{body.put("name",note.trim()).put("url",url).put("enabled",true)
      .put("monitor_device","pc").put("record_target",MODES[mode]).put("quality","origin");}catch(Exception ignored){}
  worker.execute(()->{
   JSONObject item=null;
   try{item=Bridge.call(this,"/v1/anchors",body);}
   catch(Bridge.Rejected reject){final String reason=reject.getMessage();runOnUiThread(()->status.setText(reason));return;}
   catch(Exception offline){
    try{body.put("id","local_"+UUID.randomUUID().toString().replace("-","").substring(0,12));item=body;}catch(Exception ignored){}
   }
   final JSONObject result=item;if(result==null)return;
   runOnUiThread(()->{
    try{
     boolean replaced=false;
     for(int i=0;i<anchors.length();i++){
      JSONObject old=anchors.optJSONObject(i);
      if(old!=null&&url.equals(old.optString("url"))){anchors.put(i,result);replaced=true;break;}
     }
     if(!replaced)anchors.put(result);
     currentId=result.optString("id");
     getSharedPreferences("desk",0).edit().putString("anchors",anchors.toString()).apply();
    }catch(Exception ignored){}
    lastSignature="";render();
    status.setText("phone".equals(MODES[mode])?"已添加，电脑检测开播后会通知这台手机录制":"已添加，电脑检测开播后会自动录制");
   });
  });
 }

 String normalize(String text)throws Exception{
  Matcher m=Pattern.compile("https?://[^\\s<>\"，。]+").matcher(text==null?"":text);
  if(!m.find())throw new Exception("请粘贴抖音主播主页、直播、店铺或视频分享链接");
  String s=m.group().replaceAll("[.,;!）)]+$","");
  Uri u=Uri.parse(s);String host=(u.getHost()==null?"":u.getHost().toLowerCase()),path=u.getPath()==null?"/":u.getPath();
  if(u.getUserInfo()!=null||(u.getPort()!=-1&&u.getPort()!=443&&u.getPort()!=80))
   throw new Exception("链接格式不安全");
  List<String> douyin=Arrays.asList("live.douyin.com","www.douyin.com","douyin.com","m.douyin.com","v.douyin.com","www.iesdouyin.com","webcast.amemv.com");
  if(douyin.contains(host)){
   if("live.douyin.com".equals(host)){
    if(!path.matches("/\\d{4,30}/?"))throw new Exception("请使用完整直播间房间号");
    return "https://live.douyin.com/"+path.replace("/","");
   }
   if("webcast.amemv.com".equals(host)){
    if(!path.matches("/douyin/webcast/reflow/\\d{4,30}/?"))throw new Exception("请改用直播分享链接");
    return s;
   }
   if("v.douyin.com".equals(host)){
    if(!path.matches("/[A-Za-z0-9_-]{4,80}/?"))throw new Exception("抖音分享短链不完整");
    return s;
   }
   if("www.iesdouyin.com".equals(host)){
    if(!path.matches("/share/video/\\d{5,30}/?"))throw new Exception("抖音视频分享链接不完整");
    return s;
   }
   if(path.matches("/live/\\d{4,30}/?"))return "https://live.douyin.com/"+path.split("/")[2];
   if(path.matches("/user/[A-Za-z0-9_-]{8,200}/?"))return "https://www.douyin.com"+path.replaceAll("/$","");
   if(path.matches("/video/\\d{5,30}/?"))return "https://www.douyin.com"+path.replaceAll("/$","");
   if(path.startsWith("/shop/")&&path.length()>6)return "https://www.douyin.com"+path.replaceAll("/$","");
   throw new Exception("请使用主播主页、直播间、视频或店铺分享链接");
  }
  if(Arrays.asList("haohuo.jinritemai.com","jinritemai.com").contains(host)){
   if(!(path.startsWith("/views/shop/index")||path.startsWith("/ecommerce/trade/detail")))
    throw new Exception("请使用抖音店铺首页链接");
   return s;
  }
  if("z.douyin.com".equals(host)){
   if(!path.matches("/[A-Za-z0-9_-]{4,80}/?"))throw new Exception("抖音店铺短链不完整");
   return s;
  }
  throw new Exception("当前只支持抖音主播主页、直播、店铺或视频链接");
 }

 // ---------- recording ----------

 void phoneRecord(JSONObject a){
  if(streams==null){status.setText("解析组件尚未就绪");return;}
  if(a==null){status.setText("请先添加主播");return;}
  streams.add(a);
 }

 /** Choose a room for audio-only listening; an already-recording room is allowed. */
 void listenDialog(){
  if(streams==null){status.setText("解析组件尚未就绪");return;}
  if(anchors.length()==0){status.setText("请先添加主播或直播间");return;}
  List<String> labels=new ArrayList<>();final List<JSONObject> picks=new ArrayList<>();
  for(int i=0;i<anchors.length();i++){
   JSONObject a=anchors.optJSONObject(i);if(a==null)continue;
   String name=a.optString("name").trim();if(name.isEmpty())name="待读取主播";
   String state=StreamService.captures.containsKey(a.optString("id"))?"录制中 · 边录边听":"听直播";
   labels.add(name+" · "+state);picks.add(a);
  }
  new AlertDialog.Builder(this).setTitle("听直播 · 只出声，不额外录制")
   .setItems(labels.toArray(new String[0]),(d,which)->{
    JSONObject a=picks.get(which);currentId=a.optString("id");
    AudioPlayer.stop();status.setText("正在读取 "+a.optString("name","主播")+" 的音频流…");
    streams.listen(a);
   }).setNegativeButton("取消",null).show();
 }

 /** Pick a local replay and play its audio only. */
 void listenVideoDialog(){
  JSONArray all=VideoLibrary.all(this);
  if(all.length()==0){new AlertDialog.Builder(this).setTitle("听视频").setMessage("手机里还没有录好的回放。").setPositiveButton("关闭",null).show();return;}
  List<String> labels=new ArrayList<>();final List<String> uris=new ArrayList<>();
  for(int i=all.length()-1;i>=0&&labels.size()<20;i--){
   JSONObject item=all.optJSONObject(i);if(item==null)continue;
   String name=item.optString("name","主播");
   long size=item.optLong("size")/1048576;
   labels.add(name+" · "+size+" MB"+(item.optBoolean("uploaded")?" · 已传电脑":" · 待传"));
   uris.add(item.optString("uri"));
  }
  new AlertDialog.Builder(this).setTitle("听视频 · 只播放声音")
   .setItems(labels.toArray(new String[0]),(d,which)->{
    try{AudioPlayer.playLocal(this,Uri.parse(uris.get(which)),labels.get(which));}
    catch(Exception e){status.setText("这条回放暂时不能播放："+e.getMessage());}
   }).setNegativeButton("关闭",null).show();
 }

 void listenChanged(){
  if(listenStatus==null)return;
  String text=AudioPlayer.status();
  if(text.isEmpty()){listenStatus.setVisibility(View.GONE);return;}
  listenStatus.setVisibility(View.VISIBLE);listenStatus.setText(text);
 }

 void startListening(JSONObject anchor,String url,String cookie,String agent,boolean hls,String fallback){AudioPlayer.play(this,url,cookie,agent,anchor.optString("name","主播"),hls,fallback);}

 /** Quality is chosen from the platform's published variants; low FPS is a desktop-produced copy. */
 void recordOptionsDialog(){
  String quality=getSharedPreferences("desk",0).getString("record_quality","standard");
  int fps=getSharedPreferences("desk",0).getInt("record_fps",0);
  String[] qualityLabels={"原画（最清晰，流量大）","高清","标清（默认，省流）","省流"};
  String[] qualityValues={"origin","high","standard","low"};
  String[] fpsLabels={"原帧率（推荐）","30 帧","25 帧","20 帧","15 帧","10 帧"};
  int[] fpsValues={0,30,25,20,15,10};
  int qualityIndex=0,fpsIndex=0;
  for(int i=0;i<qualityValues.length;i++)if(qualityValues[i].equals(quality))qualityIndex=i;
  for(int i=0;i<fpsValues.length;i++)if(fpsValues[i]==fps)fpsIndex=i;
  LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);
  int pad=(int)(18*getResources().getDisplayMetrics().density);box.setPadding(pad,pad/2,pad,0);
  TextView qLabel=new TextView(this);qLabel.setText("录制画质");qLabel.setTextSize(13);box.addView(qLabel);
  Spinner qSpin=new Spinner(this);ArrayAdapter<String> qAdapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,qualityLabels);qAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);qSpin.setAdapter(qAdapter);qSpin.setSelection(qualityIndex);box.addView(qSpin);
  TextView fLabel=new TextView(this);fLabel.setText("视频帧率");fLabel.setTextSize(13);fLabel.setPadding(0,pad/3,0,0);box.addView(fLabel);
  Spinner fSpin=new Spinner(this);ArrayAdapter<String> fAdapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,fpsLabels);fAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);fSpin.setAdapter(fAdapter);fSpin.setSelection(fpsIndex);box.addView(fSpin);
  TextView tip=new TextView(this);tip.setTextSize(11);tip.setTextColor(Color.rgb(120,126,130));tip.setPadding(0,pad/3,0,0);
  tip.setText("画质通过切换平台提供的不同清晰度流，录制文件的分辨率和码率会随之变化；降低帧率不会影响声音，低帧率副本由电脑在接收、校验后生成，原片保留。电脑自动录制的画质仍以电脑端设置为准。");
  box.addView(tip);
  new AlertDialog.Builder(this).setTitle("手机录制设置").setView(box).setNegativeButton("取消",null)
   .setPositiveButton("保存",(d,w)->{
    String q=qualityValues[qSpin.getSelectedItemPosition()];int f=fpsValues[fSpin.getSelectedItemPosition()];
    getSharedPreferences("desk",0).edit().putString("record_quality",q).putInt("record_fps",f).apply();
    status.setText("录制设置已保存（下次录制生效）："+qualityLabels[qSpin.getSelectedItemPosition()]+" · "+fpsLabels[fSpin.getSelectedItemPosition()]);
   }).show();
 }

 /** Turn all-day auto recording on or off straight from the phone. */
 void setAuto(JSONObject a,boolean enabled){
  final String id=a.optString("id");
  JSONObject body=new JSONObject();
  try{
   JSONArray windows=a.optJSONArray("windows");
   body.put("id",id).put("name",a.optString("name")).put("url",a.optString("url"))
       .put("enabled",enabled).put("quality",a.optString("quality","origin"))
       .put("monitor_device",a.optString("monitor_device","pc"))
       .put("record_target",a.optString("record_target","pc"))
       .put("windows",windows==null?new JSONArray():windows);
  }catch(Exception ignored){}
  worker.execute(()->{
   String error=null;
   try{Bridge.call(this,"/v1/anchors",body);}catch(Exception e){error=e.getMessage();}
   final String reason=error;
   runOnUiThread(()->{
    try{a.put("enabled",enabled);getSharedPreferences("desk",0).edit().putString("anchors",anchors.toString()).apply();}catch(Exception ignored){}
    lastSignature="";render();
    status.setText(reason!=null?"电脑未连接，改动只存在手机里":(enabled?"已开启：全天检测，开播自动录到电脑":"已关闭自动录播"));
   });
  });
 }

 /** Ask the desktop to start or stop recording this anchor. */
 void pcRecord(JSONObject a,boolean stop){
  final String id=a.optString("id");
  worker.execute(()->{
   String error=null;
   try{Bridge.call(this,"/v1/anchors/"+id+(stop?"/stop":"/start"),new JSONObject());}catch(Exception e){error=e.getMessage();}
   final String reason=error;
   runOnUiThread(()->{status.setText(reason!=null?"电脑未连接，请确认电脑端正在运行":(stop?"已通知电脑停止并保存":"已通知电脑开始录制"));lastSignature="";render();});
  });
 }

 void stopPhone(String id){if(streams!=null)streams.cancel(id);else StreamService.stop(id);}

 void cancelStream(String id){if(streams!=null)streams.cancel(id);lastSignature="";}

 // ---------- settings ----------

 void settingsDialog(){
  boolean deleteAfter=getSharedPreferences("desk",0).getBoolean("delete_uploaded",false);
  String[] items={
   "与电脑重新连接 / 同步主播",
   (independent?"关闭":"开启")+"手机自己检测（不用电脑）",
   (deleteAfter?"关闭":"开启")+"上传校验后删除手机原文件",
   RecordingService.recording?"停止并保存手机屏幕录制":"备用：录制手机屏幕（系统授权）",
   "手机录制的画质与帧率",
   "诊断信息"
  };
  new AlertDialog.Builder(this).setTitle("设置").setItems(items,(d,which)->{
   if(which==0)sync();
   else if(which==1){
    independent=!independent;
    getSharedPreferences("desk",0).edit().putBoolean("independent",independent).apply();
    nextScan=0;scanAt.clear();
    if(independent){status.setText("手机自己检测已开启：逐个检查主播，开播就直接录到手机");scan();}
    else status.setText("已关闭手机自己检测，改由电脑检测开播");
   }else if(which==2){
    if(deleteAfter){
     getSharedPreferences("desk",0).edit().putBoolean("delete_uploaded",false).apply();
     status.setText("已关闭自动删除；今后回放上传后手机原片会保留");
    }else{
     new AlertDialog.Builder(this).setTitle("开启后会自动删除手机原片")
      .setMessage("只有电脑完整接收并校验 SHA-256 后才会删除；删除后无法从手机恢复。建议先确认电脑素材库里的回放可以正常播放。")
      .setNegativeButton("取消",null)
      .setPositiveButton("开启",(x,y)->{
       getSharedPreferences("desk",0).edit().putBoolean("delete_uploaded",true).apply();
       status.setText("已开启：上传完成并校验后删除手机原片");
      }).show();
    }
   }else if(which==3)screenRecordDialog();
   else if(which==4)recordOptionsDialog();
   else diagnostics();
  }).show();
 }

 String versionName(){try{return getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception e){return "?";}}

 void diagnostics(){
  JSONArray all=VideoLibrary.all(this);
  int pending=0;for(int i=0;i<all.length();i++)if(!all.optJSONObject(i).optBoolean("uploaded"))pending++;
  String text="电脑连接："+(connection.getText().toString().startsWith("●")?"已连接":"未连接")
   +"\n自动录播：由电脑执行"
   +"\n手机录制："+StreamService.captures.size()+" / "+StreamService.limit+" 路"
   +"\n待传回电脑："+pending+" 段"
   +"\n传输状态："+StreamService.transferMessage
   +"\n版本："+versionName();
  new AlertDialog.Builder(this).setTitle("诊断信息").setMessage(text).setPositiveButton("关闭",null).show();
 }

 void screenRecordDialog(){
  JSONObject a=selected();
  String name=a==null?"手机屏幕录制":(a.optString("name").isEmpty()?"手机屏幕录制":a.optString("name"));
  if(RecordingService.recording){
   startService(new Intent(this,RecordingService.class).setAction("STOP"));
   status.setText("正在停止并保存屏幕录制");
   return;
  }
  new AlertDialog.Builder(this).setTitle("备用：录制手机屏幕")
   .setMessage("这会把手机当前画面（例如抖音 App）录成一段视频，需要系统录屏授权。只在你确实需要时才用；正常录播请用列表里的“手机录这一路”或让电脑录制。")
   .setNegativeButton("取消",null)
   .setPositiveButton("继续",(d,w)->{
    permissionPending=true;
    MediaProjectionManager m=(MediaProjectionManager)getSystemService(MEDIA_PROJECTION_SERVICE);
    startActivityForResult(m.createScreenCaptureIntent(),30);
   }).show();
 }

 void arm(){
  if(permissionPending)return;
  cancelPermission=false;
  if(RecordingService.armed)return;
  permissionPending=true;
  MediaProjectionManager m=(MediaProjectionManager)getSystemService(MEDIA_PROJECTION_SERVICE);
  startActivityForResult(m.createScreenCaptureIntent(),30);
 }

 @Override public void onActivityResult(int req,int code,Intent data){
  super.onActivityResult(req,code,data);
  if(req!=30)return;
  permissionPending=false;
  if(cancelPermission||code!=RESULT_OK||data==null){status.setText("没有授权录屏，已取消");return;}
  startForegroundService(new Intent(this,RecordingService.class).setAction("ARM").putExtra("code",code).putExtra("data",data));
  handler.postDelayed(()->{
   JSONObject a=selected();
   startService(new Intent(this,RecordingService.class).setAction("RECORD")
    .putExtra("name",a==null?"手机屏幕录制":a.optString("name"))
    .putExtra("anchor_id",a==null?"":a.optString("id")));
   status.setText("正在录制手机屏幕，停止时点这里或通知栏");
  },900);
 }

 @Override public void onRequestPermissionsResult(int request,String[] p,int[] grant){
  super.onRequestPermissionsResult(request,p,grant);
  if(request==20||request==21)permissionPending=false;
 }

 // ---------- replay ----------

 void replayDialog(){
  JSONArray all=VideoLibrary.all(this);
  if(all.length()==0){new AlertDialog.Builder(this).setTitle("回放").setMessage("手机里还没有录好的回放。").setPositiveButton("关闭",null).show();return;}
  List<String> labels=new ArrayList<>();final List<String> uris=new ArrayList<>();
  for(int i=all.length()-1;i>=0&&labels.size()<20;i--){
   JSONObject item=all.optJSONObject(i);if(item==null)continue;
   long size=item.optLong("size")/1048576;
   labels.add(item.optString("name","主播")+" · "+size+" MB"+(item.optBoolean("uploaded")?" · 已传电脑":" · 待传"));
   uris.add(item.optString("uri"));
  }
  new AlertDialog.Builder(this).setTitle("手机回放")
   .setItems(labels.toArray(new String[0]),(d,which)->{
    try{startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse(uris.get(which)),"video/mp4")
     .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));}
    catch(Exception e){status.setText("请在手机相册 Movies/VideoDesk 查看回放");}
   }).setPositiveButton("关闭",null).show();
 }

 // ---------- bridge ----------

 void sync(){
  final JSONArray local=anchors;
  worker.execute(()->{
   try{
    JSONObject r=Bridge.call(this,"/v1/config",null);
    JSONArray items=r.getJSONArray("anchors");
    JSONArray merged=new JSONArray(items.toString());int waiting=0;
    for(int i=0;i<local.length();i++){
     JSONObject a=local.optJSONObject(i);if(a==null||!a.optString("id").startsWith("local_"))continue;
     boolean exists=false;
     for(int k=0;k<items.length();k++)if(a.optString("url").equals(items.optJSONObject(k).optString("url")))exists=true;
     if(exists)continue;
     try{JSONObject body=new JSONObject(a.toString());body.remove("id");merged.put(Bridge.call(this,"/v1/anchors",body));}
     catch(Exception failure){merged.put(a);waiting++;}
    }
    final int pending=waiting;final JSONArray result=merged;
    runOnUiThread(()->{
     anchors=result;
     getSharedPreferences("desk",0).edit().putString("anchors",anchors.toString()).apply();
     lastSignature="";render();status.setText("已和电脑同步 "+result.length()+" 位主播"+(pending>0?"；"+pending+" 位待同步":""));
    });
   }catch(Exception e){
    runOnUiThread(()->status.setText("电脑未连接："+e.getMessage()));
   }
  });
 }

 void acceptConfig(JSONObject config){
  JSONArray incoming=config.optJSONArray("anchors");if(incoming==null)return;
  JSONArray merged=new JSONArray();
  for(int n=0;n<incoming.length();n++){
   JSONObject fresh=incoming.optJSONObject(n);if(fresh==null)continue;
   JSONObject old=null;
   for(int k=0;k<anchors.length();k++)if(fresh.optString("id").equals(anchors.optJSONObject(k).optString("id"))){old=anchors.optJSONObject(k);break;}
   if(old!=null&&fresh.optString("url").equals(old.optString("url")))try{
    if(old.has("native_room_id"))fresh.put("native_room_id",old.get("native_room_id"));
    if(old.has("native_room_at"))fresh.put("native_room_at",old.get("native_room_at"));
   }catch(JSONException ignored){}
   merged.put(fresh);
  }
  for(int n=0;n<anchors.length();n++){
   JSONObject local=anchors.optJSONObject(n);
   if(local==null||!local.optString("id").startsWith("local_"))continue;
   boolean exists=false;
   for(int k=0;k<merged.length();k++)exists|=local.optString("url").equals(merged.optJSONObject(k).optString("url"));
   if(!exists)merged.put(local);
  }
  boolean changed=merged.length()!=anchors.length();
  String[] fields={"id","name","url","enabled","record_target","monitor_device","quality","windows","runtime"};
  for(int n=0;!changed&&n<merged.length();n++)
   for(String field:fields)
    if(!String.valueOf(merged.optJSONObject(n).opt(field)).equals(String.valueOf(anchors.optJSONObject(n).opt(field)))){changed=true;break;}
  if(!changed)return;
  anchors=merged;
  getSharedPreferences("desk",0).edit().putString("anchors",anchors.toString()).apply();
  lastSignature="";render();
 }

 void handleCommand(JSONObject c){
  String id=c.optString("anchor_id");if(id.isEmpty())return;
  JSONObject a=null;
  for(int k=0;k<anchors.length();k++)if(id.equals(anchors.optJSONObject(k).optString("id")))a=anchors.optJSONObject(k);
  if(a==null)return;
  if("stop".equals(c.optString("type"))){stopPhone(id);return;}
  // Commands carrying a resolved stream are handled by StreamService even when
  // this page is gone, so recording never depends on the UI being visible.
  if(!c.optString("url").isEmpty())return;
  phoneRecord(a);
 }

 void poll(){
  // Keep talking to the desktop even when this page is in the background: the
  // foreground service keeps the process alive, but a UI-only poll meant the
  // phone went silent the moment the screen was off, so a live start command
  // never arrived and the first minutes of a stream were lost.
  if(polling)return;polling=true;
  worker.execute(()->{
   try{
    JSONObject config=Bridge.call(this,"/v1/config",null);
    runOnUiThread(()->{
     connection.setText(independent?"● 电脑已连接 · 检测由电脑负责，手机随时接手":"● 电脑已连接 · 自动录播在电脑执行");linked=true;
     connection.setTextColor(Color.rgb(20,107,99));
     // Clear whatever the phone-side sweep left behind: once the desktop is back
     // in charge, a stale "未取得视频流" line only confuses.
     if(independent&&StreamService.captures.isEmpty()){
      String shown=status.getText().toString();
      if(shown.contains("未取得可录制的视频流")||shown.startsWith("读取 ")||shown.startsWith("主播："))
       status.setText("电脑负责检测开播，开播后按主播设置自动录制");
     }
     acceptConfig(config);
     JSONArray commands=config.optJSONArray("commands");
     if(commands!=null)for(int i=0;i<commands.length();i++){
      JSONObject c=commands.optJSONObject(i);if(c==null)continue;
      String cid=c.optString("id");if(acceptedCommands.contains(cid))continue;
      acceptedCommands.add(cid);handleCommand(c);
     }
     Bridge.status(this,StreamService.summary(),true,null);
    });
   }catch(Exception e){
    runOnUiThread(()->{
     linked=false;connection.setText(independent?"● 手机自己检测中 · 不依赖电脑":"○ 电脑未连接 · 用 USB 连接后在设置里重试");
     connection.setTextColor(Color.rgb(150,96,20));
    });
   }finally{polling=false;}
  });
 }

 // ---------- phone-side live detection ----------

 /** Ask the resolver to look at one anchor right now (used by the Douyin notice). */
 void scanNow(JSONObject a){
  if(a==null||streams==null)return;
  if(StreamService.captures.containsKey(a.optString("id")))return;
  scanAt.put(a.optString("id"),System.currentTimeMillis());
  handler.post(()->streams.add(a));
 }

 /**
  * Round-robin live check that runs on the phone itself, so recording no longer
  * depends on the desktop being open. One anchor per pass; a pass that finds the
  * anchor offline simply moves on to the next one.
  */
 void scan(){
  long now=System.currentTimeMillis();
  if(now<nextScan||streams==null)return;
  Detector.log("界面扫描 可见="+visible+" 电脑在线="+linked+" 主播="+anchors.length());
  // A page that never answers must not freeze every later check.
  if(streams.started>0&&now-streams.started>90000){
   streams.forceNext();nextScan=now+15000;return;
  }
  // The desktop is the better detector when it is reachable (no battery cost on
  // the phone); the phone only takes over once the bridge is gone.
  if(linked){nextScan=now+60000;return;}
  int count=anchors.length();
  if(count==0){nextScan=now+60000;return;}
  for(int step=0;step<count;step++){
   int i=(scanCursor+step)%count;
   JSONObject a=anchors.optJSONObject(i);
   if(a==null)continue;
   String id=a.optString("id");
   if(!a.optBoolean("enabled",false))continue;
   if(StreamService.captures.containsKey(id))continue;
   Long at=scanAt.get(id);
   if(at!=null&&now-at<180000)continue;
   scanCursor=(i+1)%count;
   scanAt.put(id,now);
   nextScan=now+25000;
   android.util.Log.i("VideoDeskScan","检查 "+a.optString("name",id));
   if(!linked)status.setText("手机正在自行检测开播："+a.optString("name","主播"));
   streams.add(a);
   return;
  }
  nextScan=now+30000;
 }

 final Runnable tick=new Runnable(){public void run(){
  if(isFinishing())return;
  if(System.currentTimeMillis()>nextPoll){poll();nextPoll=System.currentTimeMillis()+(visible?4000:8000);}
  if(independent)scan();
  render();
  listenChanged();
  handler.postDelayed(this,1500);
 }};

 // ---------- intents / lifecycle ----------

 void acceptIntent(Intent i){
  if(i==null)return;
  if(Intent.ACTION_SEND.equals(i.getAction())&&i.getType()!=null&&i.getType().startsWith("text/")){
   String shared=i.getStringExtra(Intent.EXTRA_TEXT);
   if(shared!=null&&!shared.trim().isEmpty()){
    String url=firstUrl(shared);
    if(url.isEmpty())status.setText("没有从分享内容里找到链接");
    else if(anchorShare(url))addAnchorDialog(shared);
    else if(shortShare(url))shareChoice(shared);
    else saveVideo(shared);
   }
  }
  String endpoint=i.getStringExtra("endpoint"),key=i.getStringExtra("pairing_key");
  if("http://127.0.0.1:47631".equals(endpoint)&&key!=null&&key.length()>=20){
   if(!key.equals(getSharedPreferences("desk",0).getString("key","")))
    worker.execute(()->VideoLibrary.resetUploads(this));
   getSharedPreferences("desk",0).edit().putString("endpoint",endpoint).putString("key",key).apply();
   sync();
  }
  String online=i.getStringExtra("online_anchor");
  if(online!=null)currentId=online;
 }

 String firstUrl(String text){
  Matcher m=Pattern.compile("https?://[^\\s<>\"，。]+").matcher(text==null?"":text);
  return m.find()?m.group().replaceAll("[.,;!）)]+$",""):"";
 }

 boolean anchorShare(String value){
  try{
   Uri u=Uri.parse(value);String host=(u.getHost()==null?"":u.getHost().toLowerCase()),path=u.getPath()==null?"/":u.getPath();
   if("live.douyin.com".equals(host))return path.matches("/\\d{4,30}/?");
   if("webcast.amemv.com".equals(host))return path.matches("/douyin/webcast/reflow/\\d{4,30}/?");
   if(Arrays.asList("www.douyin.com","douyin.com","m.douyin.com").contains(host))
    return path.matches("/user/[A-Za-z0-9_-]{8,200}/?")||path.matches("/live/\\d{4,30}/?")||(path.startsWith("/shop/")&&path.length()>6);
   if(Arrays.asList("haohuo.jinritemai.com","jinritemai.com").contains(host))
    return path.startsWith("/views/shop/index")||path.startsWith("/ecommerce/trade/detail");
  }catch(Exception ignored){}
  return false;
 }

 boolean shortShare(String value){
  try{
   String host=(Uri.parse(value).getHost()==null?"":Uri.parse(value).getHost().toLowerCase());
   return "v.douyin.com".equals(host)||"z.douyin.com".equals(host);
  }catch(Exception ignored){return false;}
 }

 void shareChoice(String shared){
  new AlertDialog.Builder(this).setTitle("分享链接")
   .setMessage("这个短链接可能是视频，也可能是主播/直播/店铺。要下载视频，还是把作者加入录播？")
   .setNegativeButton("加入主播",(d,w)->addAnchorDialog(shared))
   .setPositiveButton("下载视频",(d,w)->saveVideo(shared))
   .setNeutralButton("取消",null).show();
 }

 @Override public void onNewIntent(Intent i){super.onNewIntent(i);setIntent(i);acceptIntent(i);}
 @Override public void onResume(){super.onResume();visible=true;nextPoll=0;}
 @Override public void onPause(){super.onPause();visible=false;}
 @Override public void onDestroy(){
  handler.removeCallbacksAndMessages(null);current=null;
  AudioPlayer.stop();
  if(streams!=null)streams.destroy();
  worker.shutdown();super.onDestroy();
 }
}
