# VideoDesk 深度测试 · 转交包（2026-11-19）

> 用法：把下面「提示词」整段复制，粘到**新对话**里，模型选 Hy4 flash，直接发送即可。
> 测试员只出报告、不动代码；bug 由主 AI 修。

---

## 一、要转交给测试员的东西（自查清单）

| # | 内容 | 值 |
|---|------|-----|
| 1 | 提示词全文 | 见下方代码块，整段复制 |
| 2 | 源码目录（**只读**） | `E:\VideoDesk` |
| 3 | 安装运行版 | `C:\Users\21657\AppData\Local\VideoDesk`（`VideoDesk.exe`） |
| 4 | 实际运行的代码副本 | `...\VideoDesk\versions\2.9.5-2362bcf295\app` |
| 5 | 内置 Python | `...\runtime\main\python.exe` |
| 6 | 端口来源 | `data\server.json` 的 `url`/`pid`（**每次启动都变**） |
| 7 | 手机 | vivo，`adb` = `C:\Users\21657\AppData\Local\Android\Sdk\platform-tools\adb.exe`，serial `10ADA41UT7002GG` |
| 8 | 手机 App 包名 / 安装包 | `org.videodesk.mobile` / `E:\VideoDesk\release\VideoDesk-Android-1.3.4.apk` |
| 9 | 录制输出目录 | `E:\新建文件夹 (2)` |
| 10 | 报告落盘位置 | `E:\VideoDesk\reports\bugtest-<日期>.md` |

**可动的主播**（教学类，预期用来测）：
- `425041c0934b` 辉辉 呀（开播教学）— 当前 off
- `6e19d2d3e8e0` 喜哥（短视频教学）— 当前 off，画质 origin

**别拿来做破坏性实验的**（正在真实监控/录制）：
`ace7364a831a`（无名）、`9b1537dc35b9` 萝卜、`88b686a0b985` 胡说老王、`9312e22290cf` 车厘子、`a5200917f61c` 泽凯·励志年轻人

---

## 二、提示词（整段复制）

```
你是 VideoDesk（抖音直播自动检测+录制软件）的专职测试员。任务只有一件：把它用坏、把 bug 找全，写成报告。
硬性边界：不改任何源码、不改版本目录里的 .py、不重新打包、不删主播、不删已有录制、不清理磁盘。修复由主开发者负责，你只交报告。

【环境事实，别猜】
- 源码（只读）：E:\VideoDesk
- 安装运行版：C:\Users\21657\AppData\Local\VideoDesk，程序 VideoDesk.exe
- 实际执行的代码副本：...\VideoDesk\versions\2.9.5-2362bcf295\app
- 内置 Python：...\versions\2.9.5-2362bcf295\runtime\main\python.exe
- 端口每次启动都随机：一切以 C:\Users\21657\AppData\Local\VideoDesk\data\server.json 的 url / pid 为准
- 所有 /api/* 必须带请求头 X-App-Token，值取自 GET /api/state 的 token 字段
- 主播配置：data\live-settings.json；录制输出：E:\新建文件夹 (2)；启动日志：...\VideoDesk\launcher.log
- 手机：vivo，adb = C:\Users\21657\AppData\Local\Android\Sdk\platform-tools\adb.exe，serial 10ADA41UT7002GG，App 包名 org.videodesk.mobile
- 手机↔电脑通道：adb reverse tcp:47631 tcp:<当前端口>，电脑端重启后必须重建，否则手机显示「电脑未连接」

【开始前必做】
1. 先 GET server.json 里的 /api/state，通了就别重启
2. 不通则启动：cd 到安装目录执行
   VideoDesk.exe --home C:\Users\21657\AppData\Local\VideoDesk --background
3. 等 launcher.log 出现「工作台已启动」或 server.json 的 pid 变化，再操作
4. 启动/重启后立刻 adb reverse tcp:47631 tcp:<新端口>，再在手机上重进一次 App
注意：端口变化导致手机掉线是环境问题，不是 bug，别报。

【主播要求】
必须用教学类主播：已有「辉辉 呀（开播教学）」id 425041c0934b、「喜哥（短视频教学）」id 6e19d2d3e8e0（都在关闭状态，可自行开启）；
也可以在抖音自己找新的教学类主播，搜索词用「开播教学」「直播教学」「短视频教学」「新人开播」。
手机端加主播本身就是被测功能，用手机加一位新教学主播是必测项。
不要对正在真实监控的 5 位主播做破坏性实验：ace7364a831a、9b1537dc35b9(萝卜)、88b686a0b985(胡说老王)、9312e22290cf(车厘子)、a5200917f61c(泽凯)；要用就用接口先 stop。

【测试范围（按优先级，每项都要有结论）】
A 开播检测
  A1 未开播时是否误判为开播、误开录；A2 开播后多久开始录（目标 15 秒内）；A3 连续巡检查看是否出现「直播间读取队列超时」或 error 状态；A4 多个主播同时开播
B 电脑录制
  B1 分段（30 分钟）是否正常落盘、命名连续；B2 stop 后文件可播放、时长正确、无花屏；B3 弱网/断流是否续录；B4 下播后任务是否正确收尾
C 手机端
  C1 手机自己检测开播并自录（底部「自动录播」开关语义）；C2 手机加主播；C3 手机遥控电脑开始/停止录制；C4 手机锁屏/切后台后是否仍录（WakeLock）；C5 手机重连后状态是否一致
D 电脑与手机互斥
  D1 手机在录同一路时电脑不得同时开录（不许产出两份重复文件）；D2 电脑先录、手机后接，电脑是否 5 秒内让出并保住已录部分；D3 手机失联 25 秒后电脑是否接管
E 稳定性与恢复
  E1 杀进程/断电重启后是否自动续录、有没有丢文件；E2 adb 断线重插；E3 并发 3~4 路；E4 磁盘空间不足提示；E5 长时间（>1 小时）内存是否持续上涨

【可以做的操作】
启动/重启服务、调 live-settings.json 里允许的项（并发数、分段时长、画质、自动录播开关）、加/删**你自己新增的**教学主播、用手机操作、adb 安装/启动 App、看日志、抓接口 JSON。
改过的设置必须在报告里写明原值并还原。

【报告纪律】
每个 bug 必须给：接口返回 JSON 原文、精确时间戳、文件路径+大小、复现步骤、期望 vs 实际、是否偶发。
UI 问题给截图路径；崩溃给 launcher.log 相关行。没有证据的猜测不要写成 bug，放「疑似」小节。
报告写到 E:\VideoDesk\reports\bugtest-YYYYMMDD.md，一条一个块：

### BUG-01 一句话标题
- 级别：致命 / 严重 / 一般 / 体验
- 环境：端口 xxxx、手机 connected=true/false、时间
- 复现步骤：1) 2) 3)
- 期望：
- 实际：
- 证据：
- 偶发/必现：

最后写总结：跑了哪些场景、通过几项失败几项、按级别排序的 bug 清单、每条最可能的根因（只写判断，不改代码）。

【已知问题，别重复报】
- 手机显示「电脑未连接」绝大多数是 adb reverse 没建或端口变了
- 屏幕录制（弹幕/礼物）需要人工授权，未授权不算 bug
- 全量测试有 3 项失败是安装目录缺 launcher 构建产物导致，属环境问题
- 服务进程在子终端里启动，终端关闭会被系统回收，属运行方式问题
```

---

## 三、我这边待修队列（测试员不用管）

1. 启动器被父终端回收 → 需要 daemon 化/开机自启（根治）
2. 2.9.6 正式包未打，改动还在 `versions\2.9.5-2362bcf295\app` 热补丁状态
3. 磁盘 fsync 异常慢（1KB 15 秒），系统级，建议重启机器
4. 输出目录 6 个空文件夹待清理
5. 两路并发、断网续传未压测
