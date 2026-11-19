package org.videodesk.mobile;
import android.webkit.*;
import org.json.*;
import java.util.*;

/** Resolve each requested anchor independently; never use the displayed neighbour room. */
final class StreamResolver {
 final MainActivity owner;final WebView view;final ArrayDeque<JSONObject> queue=new ArrayDeque<>();JSONObject current;int generation=0;long deadline,started;String script,mobileAgent;
 StreamResolver(MainActivity a,WebView v)throws Exception{owner=a;view=v;mobileAgent=v.getSettings().getUserAgentString();try(java.io.InputStream in=a.getAssets().open("live-resolver.js")){java.io.ByteArrayOutputStream b=new java.io.ByteArrayOutputStream();byte[] bytes=new byte[8192];int n;while((n=in.read(bytes))!=-1)b.write(bytes,0,n);script=b.toString("UTF-8");}WebSettings s=v.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMediaPlaybackRequiresUserGesture(true);v.setWebViewClient(new WebViewClient(){public boolean shouldOverrideUrlLoading(WebView w,WebResourceRequest r){String scheme=r.getUrl().getScheme();return !"https".equals(scheme)&&!"http".equals(scheme);}});}
 void add(JSONObject a){String id=a.optString("id");if(StreamService.captures.containsKey(id)){owner.status.setText("这位主播已经在录制");return;}if(current!=null&&current.optString("id").equals(id))return;for(JSONObject q:queue)if(q.optString("id").equals(id))return;if(queue.size()+StreamService.captures.size()+(current==null?0:1)>=4){owner.status.setText("手机最多同时录制 4 位主播，请先停止一路");return;}queue.add(a);next();}
 void cancel(String id){queue.removeIf(a->id.equals(a.optString("id")));if(current!=null&&id.equals(current.optString("id"))){generation++;current=null;view.stopLoading();next();}StreamService.stop(id);}
 void cancelAll(){queue.clear();generation++;current=null;view.stopLoading();StreamService.resolverMessage="";StreamService.stopAll();}
 void next(){if(current!=null||queue.isEmpty())return;current=queue.removeFirst();deadline=System.currentTimeMillis()+30000;started=System.currentTimeMillis();int gen=++generation;String url=current.optString("url");view.getSettings().setUserAgentString(url.contains("/user/")?"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36":mobileAgent);view.loadUrl(url);StreamService.resolverMessage="读取 "+label(current)+" 的独立视频流";owner.status.setText(StreamService.resolverMessage);owner.handler.postDelayed(()->poll(gen),2500);}
 void poll(int gen){if(current==null||gen!=generation)return;if(System.currentTimeMillis()>deadline){fail("未取得可录制的视频流，请检查手机网络／登录状态，或使用屏幕录制");return;}
  // Keep the clock running even when a page never calls back; otherwise one
  // stuck load would freeze every later check for the whole session.
  owner.handler.postDelayed(()->poll(gen),2000);
  JSONObject anchor=current;String expected=anchor.optString("url");view.evaluateJavascript(script.replace("__EXPECTED__",JSONObject.quote(expected)),raw->{if(gen!=generation||current!=anchor)return;try{Object parsed=new JSONTokener(raw).nextValue();if(parsed instanceof String){JSONObject r=new JSONObject((String)parsed);String url=r.optString("stream_url");if("offline".equals(r.optString("state"))){fail("这位主播当前未开播");return;}if(!url.isEmpty()){
     String cookie=CookieManager.getInstance().getCookie(view.getUrl());StreamService.record(owner,anchor,url,cookie,view.getSettings().getUserAgentString());StreamService.resolverMessage="";owner.status.setText("已开始手机录制："+anchor.optString("name")+"；可继续选择其他主播");current=null;next();return;}
    String room=r.optString("room_url");if(!room.isEmpty()&&!room.equals(view.getUrl())){view.getSettings().setUserAgentString(mobileAgent);view.loadUrl(room);}
   }}catch(Exception ignored){}});}
 static String label(JSONObject a){String n=a.optString("name").trim();return n.isEmpty()?"主播":n;}
 void fail(String message){StreamService.resolverMessage=label(current)+"："+message;owner.status.setText(StreamService.resolverMessage);current=null;next();}
 /** Drop whatever is stuck and move on, so one bad page cannot stall every later check. */
 void forceNext(){generation++;current=null;view.stopLoading();next();}
 void destroy(){generation++;current=null;queue.clear();StreamService.resolverMessage="";view.destroy();}
}
