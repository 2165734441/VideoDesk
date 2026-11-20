package org.videodesk.mobile;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * 后台被系统挂起时的叫醒通道。
 *
 * 实测：App 退到后台约一分钟后，vivo 会把整个进程的 WebView 和定时器一起挂起，
 * 只有纯 Java 线程（录制）还能跑，检测因此停摆；而任何一次「让进程活跃」的动作
 * （比如把界面拉回前台）都会立刻恢复。所以这里用系统闹钟定期把进程叫醒，
 * 每次醒来检查一两位主播，轮流把整张名单过一遍。
 */
public final class DetectAlarm extends BroadcastReceiver {
    /** 两分钟一次；配合每次看两位，九位主播大约九分钟轮完一圈。 */
    static final long INTERVAL = 120_000L;
    private static final int REQUEST = 71;

    static PendingIntent pending(Context c) {
        Intent i = new Intent(c, DetectAlarm.class).setAction("org.videodesk.mobile.DETECT");
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getBroadcast(c, REQUEST, i, flags);
    }

    static void arm(Context c) {
        try {
            AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            long at = System.currentTimeMillis() + INTERVAL;
            // 精确闹钟能穿透 Doze；没授权时退回普通闹钟，最多晚几分钟，不影响录播。
            boolean exact = true;
            if (Build.VERSION.SDK_INT >= 31) {
                try { exact = am.canScheduleExactAlarms(); } catch (Exception ignored) { exact = false; }
            }
            if (Build.VERSION.SDK_INT >= 23) {
                if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending(c));
                else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending(c));
            } else {
                am.set(AlarmManager.RTC_WAKEUP, at, pending(c));
            }
        } catch (Exception e) {
            Detector.log("闹钟排期失败：" + e);
        }
    }

    static void cancel(Context c) {
        try {
            AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
            if (am != null) am.cancel(pending(c));
        } catch (Exception ignored) {}
    }

    @Override public void onReceive(Context context, Intent intent) {
        try {
            Context app = context.getApplicationContext();
            Detector.log("闹钟唤醒");
            StreamService.wake(app);
            arm(app);
        } catch (Exception e) {
            Detector.log("闹钟唤醒失败：" + e);
        }
    }
}
