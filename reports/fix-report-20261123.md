# VideoDesk 2026-11-23 收尾修复报告

- 源码：`E:\VideoDesk`
- 目标版本：2.9.6
- 手机伴侣：1.4.4（versionCode 22）
- 记录时间：2026-11-23

## 一、结论先看

| 项 | 状态 | 结论 |
|---|---|---|
| C1 抖音收藏夹选文件夹 | 完成 | 能列出收藏夹并读取指定文件夹；真机验证 1 个文件夹、1 条视频，标题正确 |
| C2 喜欢/收藏语义 | 完成 | 抖音「喜欢」=点赞，「收藏」=收藏夹；B 站「点赞」/「收藏夹」；UI 文案已改成「我的喜欢（点赞）」 |
| C3 搜索翻页与排序 | 完成 | B 站第 2 页 + 最新排序真机通过（8 条）；抖音搜索遇到验证码会返回中文提示，不绕过 |
| C4 B 站收藏夹选择器 | 代码完成，真机验证受限 | 选择器、`fid=` 读取路径、UI 和单测已通；当前 B 站账号封禁/登录态过期，只能验到「封禁中」提示 |
| P1 手机 1.4.4 闹钟长稳 | 通过 | 锁屏 + Doze 连续 2 小时 6 分：61 次闹钟唤醒，最大间隔 155 秒，没有「链条断了」 |
| P2 自动录播开关语义 | 未决 | 已按用户要求先问，未收到回答；代码未改，保持现状 |
| P3 抖音验证码提示 | 通过 | 返回 400 + 中文可操作提示；需要用户在登录浏览器手动完成验证 |
| C9 2.9.6 正式包 | 完成 | 见下方构建结果；`version.json`、build.json、SHA256SUMS、验收记录一起更新 |
| C10 本地 git 提交 | 完成 | 本地提交完成，提交号见 `git log -1`；不 push、不建 Release、不改 README 对外简介和作者致敬段 |

全量测试：**289 passed，0 failed**（2 个弃用警告，36 个 subtests 通过）。比交接基线 261 项更好。

## 二、C1～C4：平台采集收尾

### C1 抖音收藏夹不能选具体文件夹

**真因**：抖音个人页的「收藏」有两层 tab。默认子 tab 是「视频」，显示全部收藏视频；文件夹卡片只有在点「收藏夹」子 tab 后才出现。点文件夹后页面 URL 不变，是页内展开，不能拼一个 `fid=` 的 URL 直达。

**改动**：

- `integrations/collect.py`：
  - 新增 `POST /api/collect/folders`，从页面自身的 `/aweme/v1/web/collects/list/` 响应抓 `collects_list`，字段包括 `collects_id_str`、`collects_name`、`total_number`。
  - `scan()` 增加 `folder` / `folder_name` / `folder_index` / `platform` 参数；抖音按名字或序号点击文件夹卡片，再读文件夹里的视频。
  - `listing_url()` 增加 `sort` / `page` / `publish` / `folder` 参数。
  - 新增 `pack_folder_card()`、`parse_douyin_folders()`、`parse_folder_dom()` 等纯函数。
- `static/index.html`、`static/app.js`：采集面板增加「收藏夹」一行（下拉框 + 「读取这个收藏夹」按钮），读取分两步：先拿列表，再读选中文件夹。
- `app.py`：注册 `collect.install(...)`。
- `tests/test_collect1123.py`：新增 Folders 等用例。

**验证**：

- 真机抖音收藏夹列表 1.6 秒返回 1 个文件夹（id/名称/数量正确）。
- 读取「软件制作功能术语」文件夹：1 条视频，标题为「Vibe知识大赏丨别再管所有弹出来的东西都叫“弹窗”了！」。
- 全部收藏 10 条、我的喜欢 10 条，标题均正常。
- 前端 E2E：来源选项、按钮文案、收藏夹下拉「全部视频（默认）/软件制作功能术语（1）」、读取结果标题正确。
- 0 个文件夹时：抖音回退读全部收藏，B 站直接提示先登录。

**标题脏数据的真因**：抖音卡片把点赞数、时长、标题和整段描述塞在同一个文本节点，class 是 hash，没有 `tit` 类。修复方式是给 `EXTRACT` 增加 `stripCounter` / `shorten`，长行按首个 `！/？/。` 截断，并把 `img alt`（`上传者：标题…`）去前缀后作为高权重候选。

### C2 「我的喜欢」与「我的收藏」在抖音的语义

**真因**：平台词义不同。抖音「喜欢」是点赞作品，「收藏」是收藏夹；B 站是「点赞」和「收藏夹」。原 UI 统称「我的收藏」，容易让用户以为是同一件事。

**改动**：`static/app.js` 按平台重建来源文案：

- 抖音：博主主页 / 我的喜欢（点赞）/ 我的收藏夹 / 关键词搜索
- B 站：UP 主空间 / 我的点赞 / 我的收藏夹 / 关键词搜索

**验证**：真机抖音「我的喜欢」读到 10 条点赞视频；「我的收藏」读到全部收藏 10 条；收藏夹单独读到 1 条。页面标题和内容对得上。

### C3 搜索没有翻页与筛选

**真因**：原扫描只滚当前结果页，最多读到 `limit` 条，没有排序和页码参数。

**改动**：

- `integrations/collect.py`：
  - `DOUYIN_SORTS`：综合=0、最多点赞=1、最新发布=2。
  - `DOUYIN_PUBLISH`：全部=0、一天=1、一周=7、半年=180、一年=365。
  - `BILIBILI_SORTS`：综合/最新发布/最多播放/最多收藏/最多弹幕，对应 `totalrank/pubdate/click/stow/dm`。
  - `listing_url()` 拼 `sort_type` / `publish_time` / `order` / `page`。
- `static/index.html`：增加搜索排序、页码、发布时间控件（B 站显示页码，抖音显示发布时间）。
- `static/app.js`：平台切换时重建排序选项。

**验证**：

- B 站搜索「开播」第 2 页 + 最新发布：8 条，标题正常，0.3 秒返回，URL 带 `order=pubdate&page=2`。
- 抖音搜索返回 400 验证码提示（不是程序故障），文案为「平台弹出了验证码（抖音搜索里很常见，不是程序故障），请在映格登录浏览器里手动完成验证后，再读取一次」。
- 抖音搜索结果本身因验证码未完成而未能真机读到；这是平台侧限制，不是代码回归。

### C4 B 站收藏夹缺选择器

**真因**：B 站收藏夹必须带 `fid=` 才能打开；原实现把「我的收藏」当成一个固定页面，没有先列文件夹。

**改动**：

- `integrations/collect.py` 的 `/api/collect/folders` 对 B 站抓 `fav/folder/created/list` 响应，或从侧栏 `fid=` 链接解析文件夹；选中后 `listing_url()` 拼 `https://space.bilibili.com/<mid>/favlist?fid=<fid>&ftype=create`。
- 前端复用 C1 的收藏夹下拉和「读取这个收藏夹」。
- `tests/test_collect1123.py` 覆盖 B 站文件夹解析和 `fid=` URL。

**验证与限制**：

- 当前保存的 B 站账号（DedeUserID 454711645，显示名「沙雕狗_」）的 SESSDATA 已在 2026-11-08 过期，账号显示「封禁中」。未登录的 favlist 会重定向到空间主页。
- 因此真机只验证到 400「封禁中」提示路径；文件夹列表、选中文件夹读取的代码路径和 UI 路径已由单测/前端验证，但**还没有用正常 B 站账号真机读到一个真实收藏夹**。这一项需要用户重新登录 B 站后再补一次验收。
- 注意：不要尝试绕过 B 站封禁或验证码。

## 三、P1：手机 1.4.4 闹钟自愈长稳

**安装**：

- 原机是 1.4.3（versionCode 21），源码已经是 1.4.4。用 `scripts/build_android.py` 重建，产出 `release/VideoDesk-Android-1.4.4.apk`（61912 bytes，sha256 `9641d411476b4cb7485f059512eeb40decd6c8b26fc77a06caa4f39c3d64345e`）。
- `adb install -r` 成功，versionName 1.4.4 / versionCode 22，数据保留。

**自愈链路**：

- `DetectAlarm.java`：主通道 120 秒，备通道晚 80 秒；`STALE_MS=10` 分钟判定链条断了；`REQUEST=71`，tag `*walarm*:org.videodesk.mobile.DETECT`（RTC_WAKEUP）。
- `StreamService.java`：每 3 秒看门狗；`stale() && now-lastArmAt>60000` 时重排，并每 10 分钟最多打一条「闹钟链条断了（N 分钟没唤醒来），已重新排期」。
- 开机 `BOOT_COMPLETED`、装包 `MY_PACKAGE_REPLACED` 主动接管重排。
- 心跳每轮打「心跳 可见=… 电脑=… 检测器=… 录制=…」。

**实测记录**：

- 08:35:43 日志出现「更新接管：重排检测闹钟」+「服务已就绪 界面=true」，说明 1.4.4 装包后的接管链路生效。
- 08:44:43 开始：按 HOME 退到后台、`dumpsys battery unplug` 模拟拔电、`dumpsys deviceidle force-idle` 进入 Doze、屏幕 `Display State=OFF` / `mWakefulness=Asleep`。探针每 5 分钟记录一次，直到 10:45:06，共 25 个采样点。
- 08:44:43～10:51:19 共 **61 次「闹钟唤醒」**，最大间隔 **155 秒**（09:33:50→09:36:25），没有出现「闹钟链条断了」。
- 更早的日志里 07:45:38～10:51:19 共 91 次唤醒，所有间隔都在 2～3 分钟内；没有 12 小时漂移。
- 探针期间进程 PID 一直是 37936，StreamService 前台服务存活；屏幕全程 OFF，Doze 状态 IDLE。
- 10:47:50 电脑桥接断开（`电脑在线=false`），手机在 10:47:35 / 10:48:49 / 10:51:19 连续检查了「胡说老王」「车厘子」「泽凯·励志年轻人」3 位主播，说明电脑掉线后手机会自己接管巡检；两位返回「未取得可录制的视频流」，一位重新读取主页 URL。这是现有自检回退逻辑，不是新代码。
- 日志里还有 08:19:16、09:34:14 的「后台检测器已接管」，说明界面不可见时后台检测器能起来。

**没验证到的**：

- 这次电池一直是 100%（USB 实际仍供电，只是用 `dumpsys battery unplug` 模拟拔电），所以**没有测真实电池耗电**；要测 overnight 真实掉电，需要真的拔掉 USB 并保持无线 ADB。
- 2 小时窗口内没有主播正在直播，所以「重新拉起录屏/采集」只验证到巡检接管和闹钟链，没有触发真实录制重连。真实直播的重连仍建议在下次有直播时补一场。
- 12 小时以上的 Doze 长稳没有做；2 小时结论是「没有漂移、没有断链」，不是 12 小时保证。

## 四、P2：自动录播开关语义（未决，代码未改）

已用中文选择题问用户，120 秒内没有收到回答。按用户要求没有自行拍板，代码保持现状。

**现状**（源码事实）：

- `StreamService.detectorStep()`：`if (desktopLinked) return;`——电脑在线时手机不重复检测。
- `Detector.check()`：`if (StreamService.desktopLinked || !independent()) return;`——电脑在线或「手机自己检测」没开时都不检测。
- `independent` 只影响「电脑离线时是否自检」和界面文案，不影响电脑在线时的行为。
- 界面上的「手机自己检测（不用电脑）」在电脑在线时实际不会自检，只是电脑离线时的回退开关；文案容易让人误解为「任何时候都自己检测」。

**待用户选**：

- A. 保持现在：电脑优先，手机只在电脑离线时自检（省电，推荐）。
- B. 手机完全独立：不管电脑在不在都自己巡检并录（更耗电，可能重复）。
- C. 只改文案，不改行为。

用户选择后再动代码；本轮没有改。

## 五、P3：抖音搜索验证码

已复核，`_blocked()` 返回 400 和中文提示：

> 平台弹出了验证码（抖音搜索里很常见，不是程序故障），请在映格登录浏览器里手动完成验证后，再读取一次

没有尝试绕过验证码。这是平台侧风控，不是程序 bug。

## 六、额外修的两个真问题

### 1. `configure_platform()` 在当前 Python 上会把平台信息查询打崩

**真因**：`integrations/runtime_support.py` 为了避开 WMI 卡死，把 `platform._wmi` 设为 `None`。当前开发用 Python 的 `platform.py` 在 `_wmi_query()` 里没有空值保护，下一行直接调用 `_wmi.exec_query`，于是 `platform.system()` / `machine()` / `win32_ver()` 抛 `AttributeError`。这导致全量测试里 3 项（图像去重 2 项、运行时兼容 1 项）失败。

**改动**：把 `None` 换成一个 `_DisabledWmi` 对象，`exec_query()` 主动抛 `OSError('WMI disabled')`。CPython 的 `win32_ver()` 会捕获 `OSError` 并走原生 Windows 回退，不再 AttributeError。

**验证**：原先失败的 3 项定向测试全部通过；全量 289 passed。

### 2. 2.9.6 构建白名单漏了采集模块

**真因**：`scripts/build_windows_exe.py` 的 `APP_FILES` 是白名单，之前没有 `integrations/collect.py`。`app.py` 已经 `import collect`，打出来的 EXE 会因为缺模块直接起不来。

**改动**：在 `APP_FILES` 增加 `integrations/collect.py` 和 `tests/test_collect1123.py`。

**验证**：构建日志中的 `smoke_runtimes()` 会用打包后的 Python 导入 `app.py` 依赖；构建成功后确认 `collect.py` 在 payload 里。

## 七、测试与构建

### 全量测试

命令：

```powershell
E:\VideoDesk\.local\mediacrawler-venv\Scripts\python.exe -m pytest tests -q
```

结果：**289 passed，0 failed**，2 warnings，36 subtests passed，40.73 秒。基线是 261 项、3 项 launcher 环境失败；本次比基线更好。

定向采集测试此前 23 passed；node UI 测试 6 passed。

### 2.9.6 构建

`all` 分支因官方 Python / Node 包下载卡住已停止：缓存里的 `python-3.13.17-embed-amd64.zip` 只有 831488 bytes，hash 与期望不符，Node zip 也缺失。实际改用已验证的 2.9.5 运行时跑 `bundle`，只重打应用层：

```powershell
python scripts/build_windows_exe.py bundle
```

构建结果：

- 文件：`release/VideoDesk-2.9.6-Windows-x64.exe`
- bytes：749532160（约 714.8 MiB）
- sha256：`7042212dd51c2740bd2bde2ca0dd4c394057b84b796ef2d7e50804afdac641df`
- payload_sha256：`b3190f5468b8d89141074acd093fba68d6d5ad97e18167bf616754b74599e937`
- files：16048（payload.zip 内 16049 项，含 runtime-manifest.json）
- build json：`release/VideoDesk-2.9.6-build.json`
- SHA256SUMS：`release/VideoDesk-2.9.6-SHA256SUMS.txt`
- 验收记录：`release/VideoDesk-2.9.6-acceptance.json`
- 更新说明：`release/本次更新-平台采集收尾-2.9.6.txt`

产物自检（构建后逐项核对）：

- runtime-manifest.json version=2.9.6，共 16048 个文件；payload.zip 16049 项。
- `app/version.json`、`app/integrations/collect.py`、`app/integrations/runtime_support.py`、`app/tests/test_collect1123.py`、`app/static/app.js`、`app/static/index.html`、`app/app.py` 的哈希与源码一致，且都在 manifest 中。
- payload.zip sha256 与 EXE 内 `BuildInfo.PayloadHash` 一致；EXE 实测 sha256 与 build.json 一致。
- 打包后冒烟：主运行时导入正常（`Main embedded runtime OK`）；采集桥 `{"ready": true, "javascript": "Node.js (V8)", "platforms": ["dy", "bili"]}`；Node v22.23.4；FFmpeg 7.1。
- SHA256SUMS 已重写并验证，包含 acceptance.json、build.json、EXE 三项。

## 八、还没做/需要用户做的

1. **B 站收藏夹完整真机验证**：需要用户重新登录一个正常 B 站账号，或提供一个带 `fid=` 的收藏夹链接；当前账号封禁，未完成。
2. **P2 拍板**：A/B/C 三选一；未选前保持现状。
3. **服务重启**：电脑休眠时工作台服务被系统收掉了，当前 4528 已拒绝连接。请用户双击托盘 `VideoDesk.exe` 启动；不要从终端启动。
4. **安装 2.9.6**：本轮只打正式包，没有替换用户当前运行版；用户确认后可安装。
5. **P6/P7 数据清理**：`output` 下 6 个空任务目录、两个测试主播是否保留，属于用户数据，未动，需用户确认。
6. **12 小时真实电池长稳**：如需 overnight 结论，要拔 USB + 无线 ADB 再测。
7. **README**：按用户要求未改对外简介和作者致敬段；构建前已把工作区里既有的 README 行改动还原。
8. **不 push、不建 Release**：本地提交完成，未推送远程。

## 九、证据文件

- 手机日志：`.tmp\p1-detector.log`（60 KB，07:44～10:51）
- 探针记录：`.tmp\p1-probe.log`（08:44～10:45，每 5 分钟）
- 全量测试日志：`.tmp\pytest-full-final.log`
- 构建日志：`.tmp\bundle-296.log`（实际成功的 bundle 日志）；`.tmp\build-296.log`（`all` 卡在官方包下载，已停止）
- 采集 UI 截图：`.tmp\ui-collect-folder.png`、`.tmp\ui-collect-liked.png`、`.tmp\ui-collect-bili.png`
- 抖音页面侦察：`.tmp\inspect-dy-*.json`
- 本报告：`reports\fix-report-20261123.md`
