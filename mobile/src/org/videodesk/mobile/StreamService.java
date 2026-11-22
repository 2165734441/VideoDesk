package org.videodesk.mobile;
import android.app.*;
import android.content.*;
import android.os.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;

/** Independent network recordings; switching apps or anchors never changes a stream. */
public class StreamService extends Service {
 static final ConcurrentHashMap<String,Capture> captures=new ConcurrentHashMap<>();
 static final Set<String> rawIds=ConcurrentHashMap.newKeySet();static volatile String lastCaptureMessage="",resolverMessage="";
 static volatile String transferMessage="接线并连接手机伴侣后自动传到电脑";
 static StreamService instance;final ScheduledExecutorService bridge=Executors.newSingleThreadScheduledExecutor();
 // 检测专用线程，和 bridge 分开：bridge 要等电脑回话，被拖住时不能连带检测一起停。
 final ScheduledExecutorService detect=Executors.newSingleThreadScheduledExecutor();
 // 录制看门狗专用线程：取流卡死时没人能叫醒它，只能外面断连接。
 final ScheduledExecutorService guard=Executors.newSingleThreadScheduledExecutor();
 // 界面退到后台后由这个检测器接手；它跑在服务里，所以划掉界面也不影响开播检测。
 final android.os.Handler ui=new android.os.Handler(android.os.Looper.getMainLooper());
 Detector detector;static volatile boolean desktopLinked=false;
 final Runnable detectTick=new Runnable(){
  long beat=0,healLog=0;
  public void run(){
   try{
    long now=System.currentTimeMillis();
    if(now>beat){
     beat=now+60000;
     MainActivity live=MainActivity.current;
     Detector.log("心跳 可见="+(live!=null&&live.visible)+" 电脑="+desktopLinked+" 检测器="+(detector!=null)+" 录制="+captures.size());
    }
    // 闹钟链条断了不会自己接上：系统吞掉一次唤醒后，服务还活着也没人再排期。
    // 现场就是 15:42 之后再没有一次唤醒，链条一断停了六小时。
    if(DetectAlarm.stale()&&now-DetectAlarm.lastArmAt>60000){
     DetectAlarm.arm(StreamService.this);
     if(now-healLog>600000){healLog=now;Detector.log("闹钟链条断了（"+((now-DetectAlarm.lastFireAt)/60000)+" 分钟没唤醒来），已重新排期");}
    }
    detectorStep();
   }catch(Throwable ignored){}
   ui.postDelayed(this,3000);
  }
 };
 /** 创建（首次）并驱动后台检测；主线程 Handler 和检测线程两条通道都会调它。 */
 void detectorStep(){
  if(desktopLinked)return;                                  // 电脑在线时手机不重复检测
  MainActivity live=MainActivity.current;
  if(live!=null&&live.visible&&!live.isFinishing())return;   // 界面在前台，交给界面扫
  if(detector==null){
   try{detector=new Detector(this);Detector.log("后台检测器已接管 可见="+(live!=null&&live.visible));}
   catch(Exception e){Detector.log("后台检测器创建失败："+e);return;}
  }
  try{detector.step();}catch(Throwable ignored){}
 }
 /**
  * 录制看门狗。取流线程一旦阻塞在 read 里，30 分钟的分段上限就再也不被判断到：
  * 现场有一次录制在「录制中」状态停了六个小时，一个片段都没再产出，而检测还在
  * 因为这位主播占着 captures 而跳过它。这里只在连接真的开着时动手，断开会让
  * 阻塞的 read 立刻抛异常，交给原来的重连逻辑收尾。
  */
 void guardTick(){
  long now=System.currentTimeMillis();
  for(Capture c:captures.values()){
   if(c.cancelled||c.connection==null)continue;
   if(now-c.lastDataAt>Capture.STALL_MS)c.reconnect("直播流 "+((now-c.lastDataAt)/1000)+" 秒没有数据，正在重新连接");
   else if(now-c.segmentAt>Capture.SEGMENT_MS+60000)c.reconnect("单段录满 30 分钟，正在切换到下一段");
  }
 }
 volatile long beatAt=0;
 // A partial wake lock keeps the six-second command poll running while the
 // screen is off. Without it vivo parks the CPU and the phone silently stops
 // answering the desktop, which is how the first minutes of a live got lost.
 static PowerManager.WakeLock awake;
 static void keepAwake(Context c){
  try{
   if(awake!=null&&awake.isHeld()){awake.release();}
   PowerManager pm=(PowerManager)c.getSystemService(Context.POWER_SERVICE);
   awake=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"VideoDesk:command-poll");
   awake.setReferenceCounted(false);awake.acquire(6*60*60*1000L);
  }catch(Exception ignored){}
 }
 static JSONArray active(){JSONArray a=new JSONArray();for(Capture c:captures.values())try{a.put(new JSONObject().put("anchor_id",c.anchor.optString("id")).put("name",c.anchor.optString("name")).put("bytes",c.bytes).put("seconds",(System.currentTimeMillis()-c.started)/1000).put("message",c.message));}catch(Exception ignored){}return a;}
 static String summary(){StringBuilder b=new StringBuilder("手机正在录制 "+captures.size()+" / 4 位主播");if(captures.isEmpty()&&!lastCaptureMessage.isEmpty())b.append("\n").append(lastCaptureMessage);if(!resolverMessage.isEmpty())b.append("\n").append(resolverMessage);for(Capture c:captures.values())b.append("\n").append(c.anchor.optString("name","主播")).append(" · ").append(c.bytes/1024/1024).append(" MB · ").append(c.message);return b.toString();}
 static void stop(String id){Capture c=captures.get(id);if(c!=null)c.stop();}
 static void stopAll(){for(Capture c:captures.values())c.stop();}
 static void record(Context c,JSONObject anchor,String url,String cookie,String agent){c.startForegroundService(new Intent(c,StreamService.class).setAction("RECORD").putExtra("anchor",anchor.toString()).putExtra("url",url).putExtra("cookie",cookie).putExtra("agent",agent));}
 /** 闹钟叫醒：把服务重新拉成前台，并立刻补一轮检测。 */
 static void wake(Context c){try{c.startForegroundService(new Intent(c,StreamService.class).setAction("DETECT"));}catch(Exception e){Detector.log("唤起前台服务失败："+e.getClass().getSimpleName());}}
 static void sync(Context c){c.startForegroundService(new Intent(c,StreamService.class).setAction("SYNC"));}
 static void consume(Context c,JSONObject conf){
  try{
   JSONArray commands=conf.optJSONArray("commands");MainActivity activity=MainActivity.current;
   if(activity!=null){MainActivity open=activity;open.runOnUiThread(()->open.acceptConfig(conf));}
   if(commands!=null)for(int i=0;i<commands.length();i++){JSONObject cmd=commands.getJSONObject(i);String id=cmd.optString("anchor_id");String type=cmd.optString("type");
    if("stop".equals(type)){stop(id);if(activity!=null){MainActivity open=activity;open.runOnUiThread(()->open.cancelStream(id));}Bridge.status(c,summary(),false,cmd.optString("id"));}
    else{String url=cmd.optString("url");
     // The desktop resolved this stream: record it even when the phone app is
     // closed, so a live start is never lost to a backgrounded Activity.
     if(!url.isEmpty()&&!captures.containsKey(id)){JSONObject anchor=new JSONObject().put("id",id).put("name",nameFor(conf,id));record(c,anchor,url,cmd.optString("cookie"),cmd.optString("agent"));Bridge.status(c,summary(),false,cmd.optString("id"));}
     else if(url.isEmpty()&&activity!=null){MainActivity open=activity;open.runOnUiThread(()->open.handleCommand(cmd));}
    }}
   Bridge.status(c,summary(),false,null);transferMessage=VideoLibrary.transfer(c);
  }catch(Exception e){transferMessage=e instanceof Bridge.Rejected?"回放传输："+e.getMessage():"等待 USB 连接，回放保留在手机；重连后自动续传";}
 }
 @Override public void onCreate(){super.onCreate();instance=this;Detector.attach(this);NotificationManager n=getSystemService(NotificationManager.class);n.createNotificationChannel(new NotificationChannel("streams","多主播录制与 USB 回放传输",NotificationManager.IMPORTANCE_LOW));keepAwake(this);
  bridge.scheduleWithFixedDelay(()->{try{
   if(awake==null||!awake.isHeld())keepAwake(this);
   consume(this,Bridge.call(this,"/v1/config",null));desktopLinked=true;
  }catch(Exception e){desktopLinked=false;transferMessage=e instanceof Bridge.Rejected?"回放传输："+e.getMessage():"等待 USB 连接，回放保留在手机；重连后自动续传";}
  },2,6,TimeUnit.SECONDS);
  // 检测的驱动必须有自己的通道：桥接那个线程还要等电脑回话，
  // 一旦电脑离线把调用拖住，共用同一个线程池就会让检测跟着一起停摆。
  // 这里用主线程 Handler（前台服务期间不会冻结）+ 独立心跳两条线互相兜底。
  ui.postDelayed(detectTick,5000);
  guard.scheduleWithFixedDelay(()->{try{guardTick();}catch(Throwable ignored){}},20,5,TimeUnit.SECONDS);
  detect.scheduleWithFixedDelay(()->{
   try{ui.post(()->{try{detectorStep();}catch(Throwable ignored){}});}catch(Throwable ignored){}
  },5,6,TimeUnit.SECONDS);
  // 开播检测的备份通道：界面还活着时由界面自己扫描（后台也照扫），
  // 界面被系统回收后才在这里按需创建解析组件接管，平时不占内存。
  Detector.log("服务已就绪 界面="+(MainActivity.current!=null));
  DetectAlarm.seed();
  DetectAlarm.arm(this);
 }
 static String nameFor(JSONObject conf,String id){try{JSONArray list=conf.optJSONArray("anchors");for(int i=0;list!=null&&i<list.length();i++){JSONObject a=list.optJSONObject(i);if(a!=null&&id.equals(a.optString("id")))return a.optString("name","主播");}}catch(Exception ignored){}return "主播";}
 Notification notification(){PendingIntent open=PendingIntent.getActivity(this,1,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);PendingIntent stop=PendingIntent.getService(this,2,new Intent(this,StreamService.class).setAction("STOP_ALL"),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);return new Notification.Builder(this,"streams").setSmallIcon(android.R.drawable.ic_menu_save).setContentTitle("映格："+captures.size()+" 位主播录制中").setContentText(transferMessage).setContentIntent(open).setOngoing(!captures.isEmpty()).addAction(new Notification.Action.Builder(null,"停止全部并保存",stop).build()).build();}
 void update(){getSystemService(NotificationManager.class).notify(280,notification());}
 @Override public int onStartCommand(Intent i,int flags,int id){startForeground(280,notification());if(i==null)return START_STICKY;String action=i.getAction();
  if("DETECT".equals(action)){Detector.log("闹钟唤醒：补一轮检测");try{detectorStep();}catch(Throwable ignored){}DetectAlarm.arm(this);return START_STICKY;}
  if("STOP_ALL".equals(action))stopAll();else if("RECORD".equals(action))try{JSONObject anchor=new JSONObject(i.getStringExtra("anchor"));String key=anchor.getString("id");if(captures.containsKey(key))return START_STICKY;if(captures.size()>=4)throw new IOException("手机最多同时录制 4 位主播，请先停止一路");Capture c=new Capture(anchor,i.getStringExtra("url"),i.getStringExtra("cookie"),i.getStringExtra("agent"));captures.put(key,c);new Thread(c,"record-"+key).start();Detector.log("开始录制 "+anchor.optString("name","主播")+" 当前 "+captures.size()+" 路");}catch(Exception e){transferMessage=e.getMessage();}update();return START_STICKY;}
 @Override public IBinder onBind(Intent i){return null;}
 // API 35+ calls this when the system dataSync window expires; older releases
 // never call it, so the annotation would break builds with an older platform.
 public void onTimeout(int startId,int type){
  // Never drop an in-flight recording just because the system reclaimed the
  // notification slot: keep the capture threads, then try to re-post the
  // foreground notice so the poll also survives.
  transferMessage="系统回收了后台通知，录制仍在继续；正在重新挂上通知";
  try{stopForeground(STOP_FOREGROUND_REMOVE);}catch(Exception ignored){}
  try{Context c=getApplicationContext();c.startForegroundService(new Intent(c,StreamService.class).setAction("SYNC"));}catch(Exception ignored){transferMessage="请打开伴侣以继续后台录制";}
 }
 @Override public void onTaskRemoved(Intent root){
  // 从最近任务里划掉伴侣时，录制和检测都要接着跑：把服务立刻挂回去。
  super.onTaskRemoved(root);
  try{getApplicationContext().startForegroundService(new Intent(getApplicationContext(),StreamService.class).setAction("SYNC"));}catch(Exception ignored){}
 }
 @Override public void onDestroy(){Detector.log("服务停止 录制="+captures.size());ui.removeCallbacksAndMessages(null);if(detector!=null){detector.destroy();detector=null;}stopAll();bridge.shutdownNow();detect.shutdownNow();guard.shutdownNow();instance=null;try{if(awake!=null&&awake.isHeld())awake.release();}catch(Exception ignored){}super.onDestroy();}
 final class Capture implements Runnable{
  /** 单段最长 30 分钟；看门狗在阻塞读的情况下也按这个硬上限收尾。 */
  static final long SEGMENT_MS=30*60*1000L;
  /** 直播流连续这么久没有新数据，就判定卡死并重连。 */
  static final long STALL_MS=120_000L;
  final JSONObject anchor;final String url,cookie,agent;volatile boolean cancelled=false;volatile HttpURLConnection connection;volatile long bytes=0;final long started=System.currentTimeMillis();volatile String message="连接直播视频流";
  /** 看门狗用的时间戳：最近一次收到数据、当前分段开始、最近一次主动断开。 */
  volatile long lastDataAt=System.currentTimeMillis(),segmentAt=System.currentTimeMillis(),kickAt=0;
  volatile String kickReason;
  Capture(JSONObject a,String u,String c,String ua){anchor=a;url=u;cookie=c;agent=ua;}
  void stop(){cancelled=true;HttpURLConnection h=connection;if(h!=null)h.disconnect();}
  /** 看门狗专用：断开连接让阻塞的 read 立刻返回，但不算用户主动停止。 */
  void reconnect(String why){
   if(cancelled)return;
   long now=System.currentTimeMillis();
   if(now-kickAt<20000)return;
   kickAt=now;kickReason=why;message=why;
   Detector.log(anchor.optString("name","主播")+"："+why);
   HttpURLConnection h=connection;
   if(h!=null){try{h.disconnect();}catch(Exception ignored){}}
  }
  public void run(){PowerManager.WakeLock wake=((PowerManager)getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"VideoDesk:stream");wake.acquire(6*60*60*1000L);try{int failures=0;while(!cancelled){String rid=UUID.randomUUID().toString().replace("-","");rawIds.add(rid);JSONObject meta=new JSONObject().put("id",rid).put("anchor_id",anchor.optString("id")).put("name",anchor.optString("name","主播"));File raw=new File(VideoLibrary.work(StreamService.this),rid+".flv"),metadata=new File(raw.getParentFile(),rid+".json");java.nio.file.Files.write(metadata.toPath(),meta.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
    try{URI uri=new URI(url);if(!Arrays.asList("http","https").contains(uri.getScheme())||uri.getHost()==null||!uri.getHost().matches("(?i)(?:[a-z0-9-]+\\.)*(?:douyincdn\\.com|amemv\\.com|bytecdn\\.cn|bytecdn\\.com|douyin\\.com)$"))throw new IOException("直播视频地址无效，请重新读取主播");connection=(HttpURLConnection)uri.toURL().openConnection();connection.setConnectTimeout(15000);connection.setReadTimeout(20000);connection.setRequestProperty("User-Agent",agent);connection.setRequestProperty("Referer","https://live.douyin.com/");if(cookie!=null&&!cookie.isEmpty())connection.setRequestProperty("Cookie",cookie);if(connection.getResponseCode()!=200)throw new IOException("直播源暂不可用，请重新读取主播");
     long segment=System.currentTimeMillis();segmentAt=segment;lastDataAt=segment;kickReason=null;try(InputStream in=connection.getInputStream();FileOutputStream out=new FileOutputStream(raw)){byte[] header=new byte[9];int got=0;while(got<9){int n=in.read(header,got,9-got);if(n<0)throw new IOException("直播源未返回视频");got+=n;}if(header[0]!='F'||header[1]!='L'||header[2]!='V')throw new IOException("直播源返回非视频内容，尚未开始录制");out.write(header);message="正在录制";byte[] b=new byte[65536];int n;while(!cancelled&&System.currentTimeMillis()-segment<SEGMENT_MS&&(n=in.read(b))!=-1){lastDataAt=System.currentTimeMillis();if(getExternalFilesDir(null).getUsableSpace()<512*1024*1024L){cancelled=true;throw new IOException("手机空间不足 512 MB，已停止并保存");}out.write(b,0,n);bytes+=n;}out.getFD().sync();}
     failures=0;
    }catch(Exception e){if(!cancelled){message=kickReason!=null?kickReason:(e.getMessage()==null?"网络暂时中断":e.getMessage());kickReason=null;Detector.log(anchor.optString("name","主播")+"："+message);failures++;}}finally{if(connection!=null)connection.disconnect();connection=null;}
    if(raw.isFile()&&raw.length()>1024){long saved=raw.length();message="正在保存 MP4 回放";try{VideoLibrary.finish(StreamService.this,raw,meta);message="片段已保存";Detector.log(anchor.optString("name","主播")+"：本段已保存 "+(saved/1048576)+" MB");}catch(Exception e){message="保存失败："+e.getMessage()+"；原始片段保留待恢复";Detector.log(anchor.optString("name","主播")+"："+message);cancelled=true;}}
    else{raw.delete();metadata.delete();}
    rawIds.remove(rid);if(failures>0&&!cancelled){if(failures>=5){message="连接失败 5 次，已停止；请返回伴侣重新读取直播间";break;}for(int t=0;t<50&&!cancelled;t++)Thread.sleep(100);}
   }}catch(Exception e){message="录制停止："+e.getMessage();}finally{if(wake.isHeld())wake.release();captures.remove(anchor.optString("id"),this);lastCaptureMessage=anchor.optString("name","主播")+"："+message;Detector.log("录制结束 "+lastCaptureMessage+" 已录 "+(bytes/1048576)+" MB");transferMessage=message;MainActivity activity=MainActivity.current;if(activity!=null)activity.runOnUiThread(()->{if(MainActivity.current==activity)activity.status.setText(lastCaptureMessage);});update();Bridge.status(StreamService.this,message,false,null);}}
 }
}
