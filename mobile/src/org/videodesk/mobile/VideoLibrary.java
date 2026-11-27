package org.videodesk.mobile;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import org.json.*;
import java.io.*;
import java.security.*;
import java.util.*;

/** Persistent local receipts; the original stays on the phone unless the verified-delete switch is on. */
final class VideoLibrary {
 static File work(Context c){File d=new File(c.getExternalFilesDir(Environment.DIRECTORY_MOVIES),"pending");d.mkdirs();return d;}
 static synchronized JSONArray all(Context c){try{return new JSONArray(new String(java.nio.file.Files.readAllBytes(new File(c.getFilesDir(),"recordings.json").toPath()),java.nio.charset.StandardCharsets.UTF_8));}catch(Exception e){return new JSONArray();}}
 static synchronized void put(Context c,JSONObject value)throws Exception{JSONArray a=all(c);boolean found=false;for(int i=0;i<a.length();i++)if(value.getString("id").equals(a.getJSONObject(i).optString("id"))){a.put(i,value);found=true;break;}if(!found)a.put(value);File dest=new File(c.getFilesDir(),"recordings.json"),tmp=new File(c.getFilesDir(),"recordings.tmp");try(FileOutputStream f=new FileOutputStream(tmp)){f.write(a.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));f.getFD().sync();}java.nio.file.Files.move(tmp.toPath(),dest.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING);}
 static void skip(InputStream in,long n)throws IOException{while(n>0){long got=in.skip(n);if(got>0){n-=got;continue;}if(in.read()<0)throw new EOFException("文件长度不足");n--;}}
 static String hash(Context c,Uri uri)throws Exception{MessageDigest digest=MessageDigest.getInstance("SHA-256");try(InputStream in=c.getContentResolver().openInputStream(uri)){byte[] b=new byte[262144];int n;while((n=in.read(b))!=-1)digest.update(b,0,n);}StringBuilder out=new StringBuilder();for(byte v:digest.digest())out.append(String.format(Locale.ROOT,"%02x",v&255));return out.toString();}
 static void register(Context c,Uri uri,String name,String anchor,long duration)throws Exception{register(c,uri,name,anchor,duration,0,"screen");}
 static void register(Context c,Uri uri,String name,String anchor,long duration,int fps,String quality)throws Exception{try(ParcelFileDescriptor f=c.getContentResolver().openFileDescriptor(uri,"r")){JSONObject j=new JSONObject().put("id",UUID.randomUUID().toString().replace("-","")).put("uri",uri.toString()).put("name",name).put("anchor_id",anchor).put("size",f.getStatSize()).put("duration_ms",duration).put("sha256",hash(c,uri)).put("fps",fps).put("quality",quality).put("uploaded",false);put(c,j);}}
 static void finish(Context c,File raw,JSONObject meta)throws Exception{
  ContentValues v=new ContentValues();String file="VideoDesk-"+new java.text.SimpleDateFormat("yyyyMMdd-HHmmss",Locale.ROOT).format(new Date())+"-"+meta.getString("id").substring(0,8)+".mp4";
  v.put(MediaStore.Video.Media.DISPLAY_NAME,file);v.put(MediaStore.Video.Media.MIME_TYPE,"video/mp4");v.put(MediaStore.Video.Media.RELATIVE_PATH,"Movies/VideoDesk");v.put(MediaStore.Video.Media.IS_PENDING,1);
  Uri uri=c.getContentResolver().insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI,v);if(uri==null)throw new IOException("无法创建手机回放");boolean valid=false;
  try{long duration;try(ParcelFileDescriptor f=c.getContentResolver().openFileDescriptor(uri,"rw")){duration=FlvMp4.convert(raw,f.getFileDescriptor());}
   v.clear();v.put(MediaStore.Video.Media.IS_PENDING,0);v.put(MediaStore.Video.Media.DURATION,duration);c.getContentResolver().update(uri,v,null,null);
   register(c,uri,meta.optString("name","主播"),meta.optString("anchor_id"),duration,
    c.getSharedPreferences("desk",0).getInt("record_fps",0),
    c.getSharedPreferences("desk",0).getString("record_quality","standard"));valid=true;c.getSharedPreferences("desk",0).edit().putString("last_video",uri.toString()).apply();
  }finally{if(!valid)c.getContentResolver().delete(uri,null,null);}
  java.nio.file.Files.deleteIfExists(raw.toPath());java.nio.file.Files.deleteIfExists(new File(raw.getParentFile(),meta.getString("id")+".json").toPath());
 }
 static void recover(Context c){File[] files=work(c).listFiles((d,n)->n.endsWith(".json"));if(files==null)return;for(File f:files)try{JSONObject m=new JSONObject(new String(java.nio.file.Files.readAllBytes(f.toPath()),java.nio.charset.StandardCharsets.UTF_8));File raw=new File(f.getParentFile(),m.getString("id")+".flv");if(!StreamService.rawIds.contains(m.getString("id"))&&raw.isFile()&&raw.length()>1024)finish(c,raw,m);}catch(Exception ignored){/* Retain interrupted originals for later recovery. */}}
 static void resetUploads(Context c){JSONArray a=all(c);for(int i=0;i<a.length();i++)try{JSONObject j=a.getJSONObject(i);j.put("uploaded",false);put(c,j);}catch(Exception ignored){}}
 static String transfer(Context c)throws Exception{
  JSONArray a=all(c);int waiting=0;boolean removed=false;for(int i=0;i<a.length();i++){JSONObject j=a.getJSONObject(i);if(j.optBoolean("uploaded"))continue;waiting++;
   JSONObject receipt=Bridge.call(c,"/v1/recordings/begin",j);if(!receipt.optBoolean("done")){long offset=receipt.getLong("offset"),size=j.getLong("size");try(InputStream in=c.getContentResolver().openInputStream(Uri.parse(j.getString("uri")))){skip(in,offset);byte[] b=new byte[4*1024*1024];while(offset<size){int n=0,wanted=(int)Math.min(b.length,size-offset);while(n<wanted){int count=in.read(b,n,wanted-n);if(count<0)throw new EOFException("手机原文件不完整");n+=count;}offset=Bridge.chunk(c,j.getString("id"),offset,b,n).getLong("offset");StreamService.transferMessage="USB 传输 "+(offset/1024/1024)+" / "+(size/1024/1024)+" MB";}}
    receipt=Bridge.call(c,"/v1/recordings/complete",new JSONObject().put("id",j.getString("id")));}
   if(receipt.optBoolean("done")){
    boolean remove=c.getSharedPreferences("desk",0).getBoolean("delete_uploaded",false);
    j.put("uploaded",true).put("computer_job",receipt.optString("job"));
    if(remove)try{
     int deleted=c.getContentResolver().delete(Uri.parse(j.getString("uri")),null,null);
     if(deleted>0){j.put("deleted",true).put("uri","");removed=true;}
    }catch(Exception ignored){j.put("delete_error",true);}
    put(c,j);
   }
  }return waiting>0?(removed?"回放已传到电脑并校验；手机原件已删除":"回放已传到电脑；手机原件已保留"):"回放同步完成";
 }
}
