package org.videodesk.mobile;
import android.app.*;
import android.content.*;
import android.os.*;
import android.service.notification.*;
import org.json.*;

public class LiveNoticeService extends NotificationListenerService {
 // The system keeps a bound NotificationListenerService alive even when the
 // activity is gone and the dataSync foreground window has expired, so this is
 // the one place that can keep the desktop link open all day. It polls the
 // desktop for live-start commands so a stream is never missed while the phone
 // sits in a pocket with the screen off.
 static final Handler beat=new Handler(Looper.getMainLooper());
 static volatile boolean beating=false;
 @Override public void onCreate(){super.onCreate();Detector.attach(this);startBeat();}
 @Override public void onDestroy(){beat.removeCallbacksAndMessages(null);beating=false;super.onDestroy();}
 void startBeat(){
  if(beating)return;beating=true;
  beat.postDelayed(new Runnable(){public void run(){
   new Thread(()->{try{StreamService.consume(LiveNoticeService.this,Bridge.call(LiveNoticeService.this,"/v1/config",null));}catch(Exception ignored){}},"notice-beat").start();
   // 通知监听服务由系统绑着，进程被杀或被冻结时它往往还在；顺手把检测闹钟接上，
   // 这样即使前台服务已经没了，闹钟链条也不会一直断着。
   try{if(DetectAlarm.stale()&&System.currentTimeMillis()-DetectAlarm.lastArmAt>60000)DetectAlarm.arm(LiveNoticeService.this);}catch(Exception ignored){}
   beat.postDelayed(this,10000);
  }},3000);
 }
 @Override public void onNotificationPosted(StatusBarNotification sbn){
  if(!"com.ss.android.ugc.aweme".equals(sbn.getPackageName())&&!"com.ss.android.ugc.aweme.mobile".equals(sbn.getPackageName()))return;
  Bundle b=sbn.getNotification().extras;
  String text=String.valueOf(b.getCharSequence(Notification.EXTRA_TITLE,""))+" "+String.valueOf(b.getCharSequence(Notification.EXTRA_TEXT,""));
  if(text.contains("未开播")||text.contains("开播提醒设置")||text.contains("关闭开播")||text.contains("预约"))return;
  if(!(text.contains("开播")||text.contains("正在直播")||text.contains("直播开始")))return;
  try{JSONArray all=new JSONArray(getSharedPreferences("desk",0).getString("anchors","[]"));JSONObject found=null;int matches=0;
   for(int i=0;i<all.length();i++){JSONObject a=all.getJSONObject(i);String name=a.optString("name");if(a.optBoolean("enabled")&&name.length()>=2&&text.contains(name)){found=a;matches++;}}
   // Ambiguous or unmatched nicknames never start a recording.
   if(matches!=1)return;JSONObject anchor=found;String id=anchor.optString("id");long previous=getSharedPreferences("desk",0).getLong("notice_"+id,0);if(System.currentTimeMillis()-previous<300000)return;
   getSharedPreferences("desk",0).edit().putLong("notice_"+id,System.currentTimeMillis()).apply();
   MainActivity activity=MainActivity.current;
   if(activity!=null&&activity.independent)activity.scanNow(anchor);
   new Thread(()->{try{Bridge.call(this,"/v1/events",new JSONObject().put("anchor_id",id).put("state","online").put("require_confirmation",true).put("message","抖音开播通知匹配昵称，请先确认主播身份与直播画面"));}catch(Exception ignored){}}).start();
   notifyAction(this,id,anchor.optString("name"));
  }catch(Exception ignored){}
 }
 static void notifyAction(Context c,String id,String name){
  NotificationManager nm=(NotificationManager)c.getSystemService(Context.NOTIFICATION_SERVICE);nm.createNotificationChannel(new NotificationChannel("live","主播开播提醒",NotificationManager.IMPORTANCE_DEFAULT));
  Intent intent=new Intent(c,MainActivity.class).putExtra("online_anchor",id).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
  PendingIntent open=PendingIntent.getActivity(c,id.hashCode(),intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  Notification n=new Notification.Builder(c,"live").setSmallIcon(android.R.drawable.ic_menu_slideshow).setContentTitle(name+"开播提醒").setContentText("已通知电脑；点击选择手机录制，确认直播画面后开始").setContentIntent(open).setAutoCancel(true).build();
  try{nm.notify(id.hashCode(),n);}catch(SecurityException ignored){}
 }
}
