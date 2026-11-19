package org.videodesk.mobile;
import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.hardware.display.*;
import android.media.*;
import android.media.projection.*;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import android.view.Surface;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;

public class RecordingService extends Service {
 public static volatile boolean armed=false,recording=false;public static volatile String message="未授权录屏";
 final ExecutorService tasks=Executors.newSingleThreadExecutor();MediaProjection projection;VirtualDisplay display;
 MediaCodec video,audio;AudioRecord audioInput;MediaMuxer muxer;Surface surface;ParcelFileDescriptor file;
 Thread videoThread,audioThread,feedThread;volatile boolean muxStarted=false,stopping=false;boolean withAudio=false;
 final Object muxLock=new Object();int videoTrack=-1,audioTrack=-1,width,height;long startNs;Uri uri;String anchorName="手机屏幕录制",anchorId="";
 @Override public android.os.IBinder onBind(Intent i){return null;}
 @Override public void onCreate(){super.onCreate();NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);nm.createNotificationChannel(new NotificationChannel("record","手机录屏状态",NotificationManager.IMPORTANCE_LOW));}
 Notification note(String text){PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);PendingIntent stop=PendingIntent.getService(this,1,new Intent(this,RecordingService.class).setAction("STOP"),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  return new Notification.Builder(this,"record").setSmallIcon(android.R.drawable.ic_menu_slideshow).setContentTitle("映格手机录屏").setContentText(text).setOngoing(true).setContentIntent(open).addAction(new Notification.Action.Builder(null,"停止并保存",stop).build()).build();}
 void update(String text){message=text;getSharedPreferences("desk",0).edit().putString("record_status",text).apply();((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(72,note(text));}
 @Override public int onStartCommand(Intent intent,int flags,int id){if(intent==null)return START_NOT_STICKY;String action=intent.getAction();
  if("ARM".equals(action)){
   startForeground(72,note("已授权，等待录制"),ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
   tasks.execute(()->{try{
    if(projection!=null){update("录制授权已就绪");return;}
    MediaProjectionManager manager=(MediaProjectionManager)getSystemService(MEDIA_PROJECTION_SERVICE);
    projection=manager.getMediaProjection(intent.getIntExtra("code",-1),intent.getParcelableExtra("data"));
    projection.registerCallback(new MediaProjection.Callback(){@Override public void onStop(){if(!tasks.isShutdown())tasks.execute(()->finish());}},new Handler(Looper.getMainLooper()));
    android.util.DisplayMetrics metrics=getResources().getDisplayMetrics();double scale=Math.min(1.0,1600.0/Math.max(metrics.widthPixels,metrics.heightPixels));width=((int)(metrics.widthPixels*scale)/2)*2;height=((int)(metrics.heightPixels*scale)/2)*2;
    display=projection.createVirtualDisplay("VideoDesk",width,height,metrics.densityDpi,DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,null,null,null);
    armed=true;update("已授权，等待开播；点击停止可结束授权");
   }catch(Exception e){update("录屏授权未就绪："+e.getClass().getSimpleName());finish();}});
  }else if("RECORD".equals(action))tasks.execute(()->{if(armed&&!recording){anchorName=intent.getStringExtra("name");anchorId=intent.getStringExtra("anchor_id");begin(anchorName);}});
  else if("STOP".equals(action))tasks.execute(()->finish());
  return START_NOT_STICKY;
 }
 void begin(String name){try{
  stopping=false;videoTrack=-1;audioTrack=-1;muxStarted=false;withAudio=false;
  ContentValues values=new ContentValues();String filename="VideoDesk-"+new SimpleDateFormat("yyyyMMdd-HHmmss",Locale.US).format(new Date())+"-"+UUID.randomUUID().toString().substring(0,5)+".mp4";
  values.put(MediaStore.Video.Media.DISPLAY_NAME,filename);values.put(MediaStore.Video.Media.MIME_TYPE,"video/mp4");values.put(MediaStore.Video.Media.RELATIVE_PATH,"Movies/VideoDesk");values.put(MediaStore.Video.Media.IS_PENDING,1);
  uri=getContentResolver().insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI,values);if(uri==null)throw new java.io.IOException("手机视频目录不可写");
  file=getContentResolver().openFileDescriptor(uri,"rw");muxer=new MediaMuxer(file.getFileDescriptor(),MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
  MediaFormat format=MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC,width,height);format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);format.setInteger(MediaFormat.KEY_BIT_RATE,4000000);format.setInteger(MediaFormat.KEY_FRAME_RATE,30);format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,2);
  video=MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);video.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);surface=video.createInputSurface();video.start();
  try{
   AudioPlaybackCaptureConfiguration config=new AudioPlaybackCaptureConfiguration.Builder(projection).addMatchingUsage(AudioAttributes.USAGE_MEDIA).addMatchingUsage(AudioAttributes.USAGE_GAME).build();
   AudioFormat af=new AudioFormat.Builder().setSampleRate(44100).setChannelMask(AudioFormat.CHANNEL_IN_STEREO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build();
   audioInput=new AudioRecord.Builder().setAudioFormat(af).setBufferSizeInBytes(Math.max(16384,AudioRecord.getMinBufferSize(44100,AudioFormat.CHANNEL_IN_STEREO,AudioFormat.ENCODING_PCM_16BIT)*2)).setAudioPlaybackCaptureConfig(config).build();
   MediaFormat sound=MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC,44100,2);sound.setInteger(MediaFormat.KEY_AAC_PROFILE,MediaCodecInfo.CodecProfileLevel.AACObjectLC);sound.setInteger(MediaFormat.KEY_BIT_RATE,128000);sound.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,16384);
   audio=MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);audio.configure(sound,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);audio.start();audioInput.startRecording();withAudio=true;
  }catch(Exception e){if(audioInput!=null){audioInput.release();audioInput=null;}if(audio!=null){try{audio.stop();}catch(Exception ignored){}audio.release();audio=null;}}
  startNs=System.nanoTime();recording=true;display.setSurface(surface);
  videoThread=new Thread(()->drain(video,false),"video-mux");videoThread.start();
  if(withAudio){audioThread=new Thread(()->drain(audio,true),"audio-mux");audioThread.start();feedThread=new Thread(this::feed,"playback-audio");feedThread.start();}
  update("手机录制中 · Movies/VideoDesk"+(withAudio?" · 内部声音受来源 App 权限限制":" · 无内部声音"));
 }catch(Exception e){update("录制未能开始："+e.getClass().getSimpleName());stopRecord();}}
 void feed(){byte[] buffer=new byte[8192];long framesRead=0,lastPts=0;AudioTimestamp stamp=new AudioTimestamp();
  try{while(recording){int n=audioInput.read(buffer,0,buffer.length,AudioRecord.READ_NON_BLOCKING);if(n<=0){Thread.sleep(10);continue;}long firstFrame=framesRead;framesRead+=n/4;long pts=Math.max(0,(System.nanoTime()-startNs)/1000-(n/4)*1000000L/44100);if(audioInput.getTimestamp(stamp,AudioTimestamp.TIMEBASE_MONOTONIC)==AudioRecord.SUCCESS)pts=Math.max(0,(stamp.nanoTime-startNs)/1000+(firstFrame-stamp.framePosition)*1000000L/44100);int index=audio.dequeueInputBuffer(20000);if(index<0)continue;ByteBuffer input=audio.getInputBuffer(index);input.clear();int bytes=Math.min(n,input.remaining());input.put(buffer,0,bytes);lastPts=Math.max(lastPts+1,pts);audio.queueInputBuffer(index,0,bytes,lastPts,0);}
   int i=audio.dequeueInputBuffer(50000);if(i>=0)audio.queueInputBuffer(i,0,0,Math.max(lastPts+1,(System.nanoTime()-startNs)/1000),MediaCodec.BUFFER_FLAG_END_OF_STREAM);
  }catch(Exception ignored){}
 }
 void drain(MediaCodec codec,boolean sound){MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();long deadline=Long.MAX_VALUE;
  try{while(true){if(!recording&&deadline==Long.MAX_VALUE)deadline=System.currentTimeMillis()+4000;if(System.currentTimeMillis()>deadline)break;
   int index=codec.dequeueOutputBuffer(info,20000);
   if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED){synchronized(muxLock){int track=muxer.addTrack(codec.getOutputFormat());if(sound)audioTrack=track;else videoTrack=track;if(videoTrack>=0&&(!withAudio||audioTrack>=0)){muxer.start();muxStarted=true;muxLock.notifyAll();}}continue;}
   if(index<0)continue;
   ByteBuffer output=codec.getOutputBuffer(index);
   if((info.flags&MediaCodec.BUFFER_FLAG_CODEC_CONFIG)!=0)info.size=0;
   if(info.size>0){synchronized(muxLock){long waitUntil=System.currentTimeMillis()+4000;while(!muxStarted&&recording&&System.currentTimeMillis()<waitUntil)muxLock.wait(50);if(muxStarted){output.position(info.offset);output.limit(info.offset+info.size);if(!sound)info.presentationTimeUs=Math.max(0,info.presentationTimeUs-startNs/1000);muxer.writeSampleData(sound?audioTrack:videoTrack,output,info);}}}
   boolean end=(info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0;codec.releaseOutputBuffer(index,false);if(end)break;
  }}catch(Exception ignored){}
 }
 void stopRecord(){boolean wasRecording=recording;recording=false;
  try{if(display!=null)display.setSurface(null);}catch(Exception ignored){}
  try{if(audioInput!=null)audioInput.stop();}catch(Exception ignored){}
  try{if(video!=null)video.signalEndOfInputStream();}catch(Exception ignored){}
  for(Thread t:new Thread[]{feedThread,videoThread,audioThread})if(t!=null)try{t.join(5000);}catch(Exception ignored){}
  boolean valid=false;
  synchronized(muxLock){try{if(muxer!=null&&muxStarted){muxer.stop();valid=true;}}catch(Exception ignored){}try{if(muxer!=null)muxer.release();}catch(Exception ignored){}muxer=null;muxStarted=false;}
  for(MediaCodec codec:new MediaCodec[]{video,audio})if(codec!=null){try{codec.stop();}catch(Exception ignored){}try{codec.release();}catch(Exception ignored){}}
  video=null;audio=null;if(audioInput!=null){try{audioInput.release();}catch(Exception ignored){}audioInput=null;}if(surface!=null){surface.release();surface=null;}if(file!=null){try{file.close();}catch(Exception ignored){}file=null;}
  if(uri!=null){if(valid){ContentValues done=new ContentValues();done.put(MediaStore.Video.Media.IS_PENDING,0);getContentResolver().update(uri,done,null,null);getSharedPreferences("desk",0).edit().putString("last_video",uri.toString()).apply();update("已保存到手机 Movies/VideoDesk");try{VideoLibrary.register(this,uri,anchorName==null?"手机屏幕录制":anchorName,anchorId==null?"":anchorId,0);StreamService.sync(this);}catch(Exception e){update("回放已保存；请打开伴侣重试 USB 同步");}}else{getContentResolver().delete(uri,null,null);if(wasRecording)update("录制未形成有效视频，请重新授权后重试");}uri=null;}
 }
 void finish(){if(stopping)return;stopping=true;stopRecord();armed=false;if(display!=null){display.release();display=null;}if(projection!=null){MediaProjection p=projection;projection=null;p.stop();}stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();}
 @Override public void onDestroy(){if(!tasks.isShutdown())tasks.execute(()->{if(!stopping)finish();});tasks.shutdown();super.onDestroy();}
}
