package org.videodesk.mobile;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.PowerManager;
import java.util.HashMap;
import java.util.Map;

/** Audio-only companion player: live HLS or a local replay, never a hidden video window. */
final class AudioPlayer {
 static MediaPlayer player;
 static String title="", state="", error="";
 static boolean prepared=false;
 static MainActivity owner;

 static boolean playing(){
  try{return player!=null&&prepared&&player.isPlaying();}catch(Exception ignored){return false;}
 }

 static String status(){
  if(!error.isEmpty())return error;
  if(player==null)return "";
  String name=title.isEmpty()?"正在播放":title;
  return "♪ "+name+(state.isEmpty()?"":" · "+state);
 }

 static void changed(){
  MainActivity a=owner;
  if(a!=null)a.runOnUiThread(()->a.listenChanged());
 }

 static void stop(){
  MediaPlayer old=player;player=null;prepared=false;state="";error="";title="";
  if(old!=null)try{old.stop();}catch(Exception ignored){}finally{try{old.release();}catch(Exception ignored){}}
  changed();
 }

 static void play(MainActivity a,String url,String cookie,String agent,String name,boolean hls,String fallback){
  stop();owner=a;title=name==null?"直播音频":name;
  try{
   Uri uri=Uri.parse(url);
   if(uri.getScheme()==null||(!uri.getScheme().equals("http")&&!uri.getScheme().equals("https")))
    throw new IllegalArgumentException("音频地址不是 HTTP 流");
   MediaPlayer mp=new MediaPlayer();
   mp.setAudioAttributes(new AudioAttributes.Builder()
    .setUsage(AudioAttributes.USAGE_MEDIA)
    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
   try{mp.setWakeMode(a,PowerManager.PARTIAL_WAKE_LOCK);}catch(Exception ignored){}
   Map<String,String> headers=new HashMap<>();
   headers.put("User-Agent",agent==null?"Mozilla/5.0":agent);
   headers.put("Referer","https://live.douyin.com/");
   if(cookie!=null&&!cookie.isEmpty())headers.put("Cookie",cookie);
   mp.setDataSource(a,uri,headers);
   mp.setOnPreparedListener(p->{try{p.start();prepared=true;state=hls?"HLS 直播":(url.startsWith("file:")||url.startsWith("content:")?"本地回放":"直播流");}catch(Exception e){error="播放启动失败："+e.getMessage();}changed();});
   mp.setOnErrorListener((p,what,extra)->{
    if(hls&&fallback!=null&&!fallback.isEmpty()){
     try{p.release();}catch(Exception ignored){}if(player==p)player=null;prepared=false;
     play(a,fallback,cookie,agent,name,false,"");
     return true;
    }
    error="这一路暂时不能在线听（"+(hls?"HLS":(url.startsWith("file:")||url.startsWith("content:")?"本地文件":"FLV"))+" 播放失败）；可以先录制，结束后用“听视频”听回放";prepared=false;state="";try{p.release();}catch(Exception ignored){}if(player==p)player=null;changed();return true;});
   mp.setOnCompletionListener(p->{state="播放结束";prepared=false;try{p.release();}catch(Exception ignored){}if(player==p)player=null;changed();});
   mp.setOnInfoListener((p,what,extra)->{if(what==MediaPlayer.MEDIA_INFO_BUFFERING_START)state="缓冲中";else if(what==MediaPlayer.MEDIA_INFO_BUFFERING_END)state="播放中";changed();return false;});
   player=mp;state="准备音频";changed();mp.prepareAsync();
  }catch(Exception e){
   error="音频打开失败："+e.getMessage();prepared=false;if(player!=null)try{player.release();}catch(Exception ignored){}player=null;changed();
  }
 }

 static void playLocal(MainActivity a,Uri uri,String name){
  stop();owner=a;title=name==null?"本地回放":name;
  try{
   MediaPlayer mp=new MediaPlayer();
   mp.setAudioAttributes(new AudioAttributes.Builder()
    .setUsage(AudioAttributes.USAGE_MEDIA)
    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
   try{mp.setWakeMode(a,PowerManager.PARTIAL_WAKE_LOCK);}catch(Exception ignored){}
   mp.setDataSource(a,uri);
   mp.setOnPreparedListener(p->{try{p.start();prepared=true;state="本地回放";}catch(Exception e){error="播放启动失败："+e.getMessage();}changed();});
   mp.setOnErrorListener((p,what,extra)->{error="这条回放暂时不能播放";prepared=false;try{p.release();}catch(Exception ignored){}if(player==p)player=null;changed();return true;});
   mp.setOnCompletionListener(p->{state="播放结束";prepared=false;try{p.release();}catch(Exception ignored){}if(player==p)player=null;changed();});
   player=mp;state="准备音频";changed();mp.prepareAsync();
  }catch(Exception e){error="回放打开失败："+e.getMessage();prepared=false;player=null;changed();}
 }
}
