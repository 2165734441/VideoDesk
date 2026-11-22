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
 *
 * 1.4.4 修的是「链条一断就再也接不上」：
 * 现场日志里 15:42 之后就再没有一次唤醒，闹钟仍挂在系统里但逾期 12 小时没送达，
 * 服务自己也不知道链条断了，于是一停就是六小时。现在：
 *  - 主通道精确两分钟一次，备通道晚 80 秒，两条都断才算真断；
 *  - 记下最后一次唤醒的时间，服务活着就自己补排；
 *  - 开机和「装完新包」都重新接管（更新完成后进程被杀，之前没人接手）。
 */
public final class DetectAlarm extends BroadcastReceiver {
    /** 两分钟一次；配合每次看两位，九位主播大约九分钟轮完一圈。 */
    static final long INTERVAL = 120_000L;
    /** 备通道故意错开：系统偶尔会吞掉某一次唤醒，错开才不会一起沉掉。 */
    private static final long BACKUP_DELAY = 80_000L;
    /** 超过这么久没有任何一次唤醒，就认定链条断了。给耗电策略留足余地。 */
    static final long STALE_MS = 10 * 60_000L;
    private static final int REQUEST = 71;

    /** 最后一次真正收到唤醒的时间；只有它会前进，所以能用来判断链条是否断了。 */
    static volatile long lastFireAt = 0L;
    /** 最后一次排期的时间，避免看门狗每秒都重排一遍。 */
    static volatile long lastArmAt = 0L;

    static PendingIntent pending(Context c) {
        return pending(c, REQUEST);
    }

    private static PendingIntent pending(Context c, int request) {
        Intent i = new Intent(c, DetectAlarm.class).setAction("org.videodesk.mobile.DETECT");
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getBroadcast(c, request, i, flags);
    }

    static void arm(Context c) {
        // 链条已经断了就让它立刻补一次，而不是再等两分钟：一来漏掉的这一轮马上补上，
        // 二来补排不会把「还没到点」的闹钟一直往后推，反而把它卡死在没触发那一格。
        boolean catchUp = stale();
        arm(c, pending(c, REQUEST), catchUp ? 1_000L : INTERVAL, true);
        arm(c, pending(c, REQUEST + 1), catchUp ? 5_000L : INTERVAL + BACKUP_DELAY, false);
        lastArmAt = System.currentTimeMillis();
    }

    private static void arm(Context c, PendingIntent pi, long delay, boolean exact) {
        try {
            AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            long at = System.currentTimeMillis() + delay;
            // 精确闹钟能穿透 Doze；没授权时退回普通闹钟，最多晚几分钟，不影响录播。
            boolean precise = exact;
            if (exact && Build.VERSION.SDK_INT >= 31) {
                try { precise = am.canScheduleExactAlarms(); } catch (Exception ignored) { precise = false; }
            }
            if (Build.VERSION.SDK_INT >= 23) {
                if (precise) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
                else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
            } else {
                am.set(AlarmManager.RTC_WAKEUP, at, pi);
            }
        } catch (Exception e) {
            Detector.log("闹钟排期失败：" + e);
        }
    }

    /** 服务还活着、但闹钟已经很久没响：链条断了，需要补排。 */
    static boolean stale() {
        return System.currentTimeMillis() - lastFireAt > STALE_MS;
    }

    /** 进程刚起来时给链条一个宽限期，别一启动就误报「断了」。 */
    static void seed() {
        if (lastFireAt == 0L) lastFireAt = System.currentTimeMillis();
    }

    static void cancel(Context c) {
        try {
            AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            am.cancel(pending(c, REQUEST));
            am.cancel(pending(c, REQUEST + 1));
        } catch (Exception ignored) {}
    }

    @Override public void onReceive(Context context, Intent intent) {
        Context app = context.getApplicationContext();
        // 冷唤醒时服务还没起来，日志要能落盘才有现场可查。
        Detector.attach(app);
        String action = intent == null ? null : intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            // 开机和「装完新包」是系统唯一还会主动叫起这个进程的两个时机。
            // 1.4.3 就是在更新之后没人接手，检测一直停到下一次手动打开。
            Detector.log(Intent.ACTION_BOOT_COMPLETED.equals(action) ? "开机接管：重排检测闹钟" : "更新接管：重排检测闹钟");
            seed();
            arm(app);
            StreamService.wake(app);
            return;
        }
        lastFireAt = System.currentTimeMillis();
        try {
            Detector.log("闹钟唤醒");
            StreamService.wake(app);
        } catch (Exception e) {
            Detector.log("闹钟唤醒失败：" + e);
        }
        // 无论这一次唤醒是否办成事，都要把下一次排上，否则链条就断在这里。
        arm(app);
    }
}
