package org.videodesk.mobile;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.util.HashMap;
import java.util.Map;

/**
 * 常驻前台服务的开播检测：只要服务活着，界面是否可见、是否被划掉都不影响检查。
 *
 * 界面在前台时由 MainActivity 自己的解析器负责（那里已经有可见的 WebView）；
 * 界面退到后台或被系统回收后，改由这里接管，因此同一位主播不会被两边同时检测。
 */
final class Detector {
    static final String DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36";
    /** 单个主播最多看这么久；页面一直不回话也要让位给下一位。 */
    private static final long PER_ANCHOR_MS = 30000L;
    /** 同一位主播两次检查之间至少隔这么久，避免刚失败就反复请求。 */
    private static final long REVISIT_MS = 180000L;

    private final Context ctx;
    private final WebView view;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Map<String, Long> looked = new HashMap<>();
    private String script;
    private String phoneAgent;
    private JSONObject current;
    private int generation;
    private int cursor;
    private long deadline;
    private long nextScan;

    Detector(Context context) throws Exception {
        ctx = context.getApplicationContext();
        view = new WebView(ctx);
        phoneAgent = view.getSettings().getUserAgentString();
        try (java.io.InputStream in = ctx.getAssets().open("live-resolver.js")) {
            java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int read;
            while ((read = in.read(chunk)) != -1) buffer.write(chunk, 0, read);
            script = buffer.toString("UTF-8");
        }
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        view.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView w, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !"https".equals(scheme) && !"http".equals(scheme);
            }
        });
    }

    /** 每两秒被服务推一次；界面回到前台后自动让位。 */
    void step() {
        long now = System.currentTimeMillis();
        if (uiAlive()) {
            if (now > nextScan) {
                log("界面还在，本轮交给界面检测");
                nextScan = now + 60000;
            }
            return;
        }
        if (current != null) {
            if (now > deadline) end(label(current) + "：未取得可录制的视频流");
            return;
        }
        if (now < nextScan) return;
        // 电脑在线时由电脑负责检测，手机不重复烧电；手机自己检测没打开时也不动。
        if (StreamService.desktopLinked || !independent()) {
            log("暂不检测 电脑在线=" + StreamService.desktopLinked + " 手机自检=" + independent());
            nextScan = now + 60000;
            return;
        }
        JSONArray list = anchors();
        int count = list.length();
        if (count == 0) {
            nextScan = now + 60000;
            return;
        }
        for (int offset = 0; offset < count; offset++) {
            int index = (cursor + offset) % count;
            JSONObject anchor = list.optJSONObject(index);
            if (anchor == null) continue;
            String id = anchor.optString("id");
            if (!anchor.optBoolean("enabled", false)) continue;
            if (StreamService.captures.containsKey(id)) continue;
            Long at = looked.get(id);
            if (at != null && now - at < REVISIT_MS) continue;
            cursor = (index + 1) % count;
            looked.put(id, now);
            nextScan = now + 25000;
            begin(anchor);
            return;
        }
        nextScan = now + 30000;
    }

    /** 没有服务实例时的落盘上下文：闹钟冷唤醒、更新接管都发生在服务起来之前。 */
    private static volatile Context logContext;

    static void attach(Context context) {
        if (context != null) logContext = context.getApplicationContext();
    }

    /**
     * 排查用：vivo 等系统会把第三方应用日志整段吞掉，所以同时写一份到应用目录，
     * 出问题时可以从手机的 Android/data/org.videodesk.mobile/files/detector.log 取。
     */
    static void log(String message) {
        android.util.Log.e("VDDetect", message);
        Context context = StreamService.instance != null ? StreamService.instance : logContext;
        if (context == null) return;
        try {
            java.io.File file = new java.io.File(context.getExternalFilesDir(null), "detector.log");
            if (file.length() > 128 * 1024) file.delete();
            String line = new java.text.SimpleDateFormat("MM-dd HH:mm:ss", java.util.Locale.US)
                    .format(new java.util.Date()) + " " + message + "\n";
            try (java.io.FileOutputStream out = new java.io.FileOutputStream(file, true)) {
                out.write(line.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
        }
    }

    private void begin(JSONObject anchor) {
        current = anchor;
        deadline = System.currentTimeMillis() + PER_ANCHOR_MS;
        int gen = ++generation;
        String url = anchor.optString("url");
        log("检查 " + label(anchor) + " " + url);
        view.getSettings().setUserAgentString(url.contains("/user/") ? DESKTOP_UA : phoneAgent);
        view.loadUrl(url);
        StreamService.resolverMessage = "后台检测：" + label(anchor);
        probe(gen);
    }

    private void probe(int gen) {
        JSONObject anchor = current;
        if (anchor == null || gen != generation) return;
        if (System.currentTimeMillis() > deadline) {
            end(label(anchor) + "：未取得可录制的视频流");
            return;
        }
        handler.postDelayed(() -> probe(gen), 2000);
        String expected = anchor.optString("url");
        view.evaluateJavascript(script.replace("__EXPECTED__", JSONObject.quote(expected)), raw -> {
            if (gen != generation || current != anchor) return;
            try {
                Object parsed = new JSONTokener(raw).nextValue();
                if (!(parsed instanceof String)) return;
                JSONObject result = new JSONObject((String) parsed);
                String stream = result.optString("stream_url");
                if ("offline".equals(result.optString("state"))) {
                    end(label(anchor) + "：这位主播当前未开播");
                    return;
                }
                if (!stream.isEmpty()) {
                    String cookie = CookieManager.getInstance().getCookie(view.getUrl());
                    log("开播，开始录制 " + label(anchor));
                    StreamService.record(ctx, anchor, stream, cookie, view.getSettings().getUserAgentString());
                    StreamService.resolverMessage = "";
                    current = null;
                    nextScan = System.currentTimeMillis() + 8000;
                    return;
                }
                String room = result.optString("room_url");
                if (!room.isEmpty() && !room.equals(view.getUrl())) {
                    view.getSettings().setUserAgentString(phoneAgent);
                    view.loadUrl(room);
                }
            } catch (Exception ignored) {
                // 页面还没把数据挂上去，下一轮再看。
            }
        });
    }

    private void end(String message) {
        log(message);
        StreamService.resolverMessage = message;
        current = null;
        nextScan = System.currentTimeMillis() + 5000;
    }

    private boolean independent() {
        return ctx.getSharedPreferences("desk", 0).getBoolean("independent", false);
    }

    private boolean uiAlive() {
        MainActivity activity = MainActivity.current;
        // 只有界面真的显示在用户眼前时才让位；退到后台、锁屏、被系统回收都算没了，
        // 否则界面一旦被限流，两边都不检测，开播就被漏掉。
        return activity != null && activity.visible && !activity.isFinishing();
    }

    private JSONArray anchors() {
        try {
            return new JSONArray(ctx.getSharedPreferences("desk", 0).getString("anchors", "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    static String label(JSONObject anchor) {
        String name = anchor.optString("name").trim();
        return name.isEmpty() ? "主播" : name;
    }

    /** 界面回到前台后清掉上一次的半截状态，避免旧结果干扰新一轮。 */
    void reset() {
        generation++;
        current = null;
        nextScan = 0;
    }

    void destroy() {
        generation++;
        current = null;
        handler.removeCallbacksAndMessages(null);
        try {
            view.loadUrl("about:blank");
            view.destroy();
        } catch (Exception ignored) {
        }
    }
}
