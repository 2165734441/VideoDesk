package org.videodesk.mobile;
import android.content.*;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

final class Bridge {
 static final class Rejected extends IOException {Rejected(String s){super(s);}}
 static JSONObject call(Context c,String path,JSONObject body)throws Exception {
  android.content.SharedPreferences p=c.getSharedPreferences("desk",0);
  String endpoint=p.getString("endpoint",""),key=p.getString("key","");
  if(!endpoint.equals("http://127.0.0.1:47631")||key.length()<20)throw new IOException("尚未通过电脑 USB 配对");
  HttpURLConnection h=(HttpURLConnection)new URL(endpoint+path).openConnection();
  h.setConnectTimeout(7000);h.setReadTimeout(12000);h.setRequestProperty("X-Phone-Key",key);
  try{
   if(body!=null){h.setRequestMethod("POST");h.setDoOutput(true);h.setRequestProperty("Content-Type","application/json; charset=utf-8");try(OutputStream out=h.getOutputStream()){out.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
   int status=h.getResponseCode();InputStream stream=status>=400?h.getErrorStream():h.getInputStream();
   ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;
   if(stream!=null)try(InputStream in=stream){while((n=in.read(b))>0){out.write(b,0,n);if(out.size()>1024*1024)throw new IOException("电脑响应过大");}}
   JSONObject result=new JSONObject(out.toString("UTF-8"));if(status>=400)throw new Rejected(result.optString("error","电脑连接失败"));return result;
  }finally{h.disconnect();}
 }
 static JSONObject chunk(Context c,String id,long offset,byte[] bytes,int n)throws Exception{
  android.content.SharedPreferences p=c.getSharedPreferences("desk",0);String endpoint=p.getString("endpoint",""),key=p.getString("key","");if(!endpoint.equals("http://127.0.0.1:47631")||key.length()<20)throw new IOException("尚未 USB 配对");
  HttpURLConnection h=(HttpURLConnection)new URL(endpoint+"/v1/recordings/chunk/"+id).openConnection();h.setConnectTimeout(7000);h.setReadTimeout(30000);h.setRequestMethod("POST");h.setDoOutput(true);h.setFixedLengthStreamingMode(n);h.setRequestProperty("X-Phone-Key",key);h.setRequestProperty("X-Offset",Long.toString(offset));h.setRequestProperty("Content-Type","application/octet-stream");
  try{try(OutputStream out=h.getOutputStream()){out.write(bytes,0,n);}int code=h.getResponseCode();if(code!=200)throw new IOException("USB 传输暂未完成，重连后自动续传（HTTP "+code+"）");try(InputStream in=h.getInputStream()){ByteArrayOutputStream data=new ByteArrayOutputStream();byte[] b=new byte[1024];int count;while((count=in.read(b))!=-1){data.write(b,0,count);if(data.size()>8192)throw new IOException("传输响应无效");}return new JSONObject(data.toString("UTF-8"));}}finally{h.disconnect();}
 }
 static void status(Context c,String message,boolean monitoring,String ack){try{JSONArray active=StreamService.active();JSONObject j=new JSONObject().put("message",RecordingService.recording?message:StreamService.summary()).put("monitoring",monitoring).put("recording",RecordingService.recording||active.length()>0).put("active",active).put("capabilities",new JSONObject().put("parallel_streams",4).put("usb_replay_import",true)).put("transfers",StreamService.transferMessage);if(ack!=null)j.put("ack",ack);call(c,"/v1/status",j);}catch(Exception ignored){}}
}
