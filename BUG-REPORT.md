# 映格 VideoDesk 2.7.0 独立测试 · BUG-REPORT

**测试性质**：独立软件测试工程师，实际操作 + 自动化黑盒 + 源码级确定性复现 + 源码审查。
**本轮范围**：只测试、只提交证据。未修改产品代码、未重建发布包、未发布、未替换用户桌面软件、未清理用户文件。
**报告时区**：全文时间均为 **UTC+08:00（北京时间）**。测试窗口 2026-11-14 14:28 — 15:30。

---

## 0. 测试对象与环境（实测，非引用历史报告）

| 对象 | 文件 | 实测 SHA256 | 与本次目标一致 |
| --- | --- | --- | --- |
| Windows x64 2.7.0 | `release/VideoDesk-2.7.0-Windows-x64.exe`（749,388,800 B） | `676a999a093e07d901be809fcb7bcc685de92be703b8e04493427ab3edb97d2d` | ✅ |
| Android 1.0.0 | `release/VideoDesk-Android-1.0.0.apk`（37,267 B） | `dbfa13c3c270544f95dc543995fb18b326e58a80ad3178372f4977aa86c85c61` | ✅ |
| 用户当前安装的 EXE | `<USERPROFILE>\AppData\Local\VideoDesk\VideoDesk.exe` | 同上 | ✅（仅只读核对，未启动/未停止） |
| 手机已装伴侣 APK | `org.videodesk.mobile` 1.0.0 (versionCode 1) | 同上 | ✅（仅只读 `pm path` + `sha256sum`） |

payload_sha256 `e5df71c0b18b2992c4e38912b3a4a65cd9b31e63cbf88d2a94725f01067abe9e`；运行实例自报版本 2.7.0。
`E:/VideoDesk` 源码与发布包内 `app/` 同名文件逐一 SHA256 比对 **11/11 完全一致**（`repro/evidence/payload_match.json`），因此下文源码行号对本 EXE 有效。

**主机**：Windows NT 10.0.26200.0 x64；AMD Ryzen 5 5600（12 逻辑核）；RAM 15.95 GB，测得可用 3.99 GB（占用 74%）；NVIDIA RTX 5060 8151 MiB，驱动 616.64，CUDA UMD 13.4；磁盘 C: 剩 49 GB / 476 GB，E: 剩 1.7 TB / 1.9 TB；Edge 157.0.4341.46、Chrome 157.0.8103.78。
**工具链**：测试 shell Python 3.13.14（缺 fastapi/imagehash/yt_dlp）、Node 22.22.2；发布包内置 Python 3.13.17、Node 22.23.4、FFmpeg 7.1、ffprobe；系统 PATH 无 ffmpeg/adb。ADB 由隔离实例 `phone-settings.json` 指向 `D:/rapoo-otg/tools/platform-tools/adb.exe`，仅用于只读查询。
**组件状态**：首次启动 9/9 组件就绪、`degraded=false`；隔离 home **未安装** AI/翻译组件与 4 个模型。硬件试编码：`h264_nvenc 可用`、`h264_qsv/amf 不可用`、选定 `h264_nvenc`。
**手机**：vivo V2304A (PD2304)，Android 16 / SDK 36，补丁 2026-08-01；屏幕 Awake；`dumpsys media_projection` 无活动投影（未录制）；已装抖音。该机**已与用户正在运行的正式实例配对**。
**网络**：测试 shell 存在 HTTP(S)_PROXY（值已脱敏），无 `no_proxy`；未改系统服务/防火墙/PATH/全局代理；隔离实例没有抖音登录态。
**浏览器**：无系统级浏览器版本写入报告外；EXE 使用内置/Edge。

**隔离**：所有操作在 `E:\VideoDesk\repro\qa\`（home / output / materials）与 `E:\VideoDesk\repro\` 内。启动前预置 `home/data/settings.json` 的 `output` 为 QA 目录，并在实例启动后再次用 `GET /api/state` 确认 `settings.output` 指向 QA（未指向文档「映格素材」）。测试实例地址一律从该实例 `data/server.json` 读取：三次启动分别为 `127.0.0.1:14545`、`127.0.0.1:7993`、`127.0.0.1:2746`，**未硬编码** 7485/7825/7826 等历史端口。三次 `--self-test` 均在约 10 分钟正常超时退出（exit 0），**不计为崩溃**。
保护哨兵 7 个（原片 / 用户新增 / 共享 / 生成副本 / 零字节 / 损坏媒体 / 中文空格逗号长名），哈希见 `repro/evidence/sentinels.json`；全部测试结束后复核 `ok=true`（`repro/scripts/verify_sentinels.py`）。

**历史资料处理**：`release/VideoDesk-2.7.0-acceptance.json`（175 Python 测试、19 前端/手机检测、真机录制）**仅作为背景**，未作为本轮任何 PASS。仓库内无 `VD27-*` 形式的历史 Bug 报告，故本轮为独立测试。

---

## 1. 真实复现缺陷（6 个）

> 说明：`VD27-001/004/006` 为**发布包黑盒实测**；`VD27-002/003/005` 为**发布包内置 Python 对 EXE 内同源模块的函数级确定性实测**（同一输入连续 2 次结果完全一致，见 `source_repro_run1/2.json`）。三者都在合成输入下复现，不依赖平台登录；其完整 UI / 真实平台链路因无登录态列为 BLOCKED，见第 5、6 节。

### VD27-001 同一房间号的不同合法链接产生多个主播记录（身份去重失效）

- **严重性**：中高。造成主播列表重复、自动监控重复轮询；`Manager.record()` 的 `room_id` 去重（`integrations/live.py:249`）只在解析出 room_id 时兜底，短链接/reflow 未解析出 room_id 时可产生重复录制尝试。**不阻断主要功能，有可靠绕过**（手动只保留一条）。
- **构建/环境**：EXE `676a999a…d2d`；UTC+8 2026-11-14 15:18；实例 `127.0.0.1:2746`；合成输入，无样例文件哈希（纯字符串）。
- **输入参数**：房间 `7381928374650910213`（19 位精确字符串）的 6 种合法形式：`live.douyin.com/<rid>`、`www.douyin.com/live/<rid>`、`live.douyin.com/<rid>?utm_source=copy`、带尾斜杠、`webcast.amemv.com/douyin/webcast/reflow/<rid>/`（https / http / 带 `?from=share`）。
- **最短复现**：清空主播 → 依次 `POST /api/live/anchors` 六个链接 → `GET /api/live`。
- **预期**：按稳定身份（实际 room ID / secUid）去重，只保留 1 条；重复提交返回 409。
- **实际**：`live.douyin.com` 与 `www.douyin.com/live/` 正确归一并被 409 拒绝；但 3 个 `webcast.amemv.com reflow` 变体各自新增成功，**共保留 4 条同房间记录**。第二轮改用房间 `7381928374650910777`、反序先 reflow 后 live，两次 POST 均 200、共 2 条。
- **复现次数**：**2/2**（另有 `source_repro.json` 第 3 次独立验证：6 种输入归一为 5 个不同字符串）。
- **证据**：`repro/evidence/matrix_raw.json`（A-dedup-P01…P06、A-dedup2-*）、`repro/evidence/source_repro.json`、`repro/logs/matrix_run_final.log`、`TEST-MATRIX.csv` A-001 / A-002。
- **已证实根因**：`integrations/live_browser.py:21-27` 只把 `live.douyin.com/<rid>` 和 `www.douyin.com/live/<rid>` 规范化为 `https://live.douyin.com/<rid>`；`:28-30` 对 `webcast.amemv.com` 仅做格式校验后 `return url.split('#',1)[0]`，保留 scheme、尾斜杠与查询串。`integrations/live.py:116` 的 `Manager.put()` 只按 `url.rstrip('/')` 字符串比较去重。调用路径：`POST /api/live/anchors` → `Anchor.validate_anchor` → `live_browser.normalize` → `Manager.put`。
- **影响**：列表误导、监控与录播名额被重复占用、用户可能给同一主播配置冲突的定时/设备策略。
- **绕过**：手动只添加/保留一个链接；添加后到主播列表检查并移除重复项。
- **修复建议**：`normalize()` 对 `webcast.amemv.com/douyin/webcast/reflow/<digits>` 同样提取数字并返回 `https://live.douyin.com/<rid>`；对 `v.douyin.com` 短链在解析到落地页后回填稳定身份；`Manager.put()` 除 URL 外增加 `identity_key`（room ID 优先、secUid 次之）去重，并保留历史数据迁移。
- **验收条件**：上述 6 个链接任意顺序添加后 `/api/live` 中同房间记录数恒为 1；`http`/`https`、查询参数、尾斜杠均不改变身份；19 位 room ID 在 API 与持久化文件中逐字符不变；不同房间号、同名不同 secUid 不被合并。

### VD27-002 换号后混入上一个账号的关注数据

- **严重性**：**高（本轮最高）**。跨账号数据混淆，可能把上一账号的关注列表展示、并批量导入到当前账号的主播列表；界面会出现「已读取 3 位，账号显示共 1 位」这类自相矛盾的数量。**有条件触发**（见前置），有绕过但用户不易察觉。
- **构建/环境**：EXE `676a999a…d2d`（同源模块）；发布包内置 Python 3.13.17；UTC+8 2026-11-14 15:00 与 15:16 两次。
- **前置/输入**：账号 A 的 following 响应带 `sec_user_id`，读得 alice/bob（2 人）；随后登录账号 B，其 following 响应**不携带 owner 查询参数**（网页常见形态），只读得 charlie（1 人），`profile.total=1`，两次调用间隔 < 900 s。
- **最短复现**：`python repro/scripts/source_repro.py`（发布包内置 Python），查看 `A2_account_switch_without_owner_param`。
- **预期**：切换账号后只含 charlie，`loaded_count<=expected_count`，不出现上一账号用户。
- **实际**：`users=[alice,bob,charlie]`、`loaded_count=3`、`expected_count=1`、`complete=false`、`owner` 仍为 `SEC_OWNER_AAAA`，消息 `已读取 3 位关注，账号显示共 1 位；尚未确认完整…`。
- **复现次数**：**2/2**（`source_repro_run1.json` 与 `source_repro_run2.json` 结果完全相同）。
- **证据**：`repro/evidence/source_repro_run1.json`、`repro/evidence/source_repro_run2.json`、`repro/scripts/source_repro.py`。
- **已证实根因**：`integrations/following.py:30` 的 `reset = stale or (owner and previous.get('owner') and owner!=previous['owner']) or first`；新响应 `owner=None` 时该条件为假，`:31-33` 沿用旧 `state['users']` 并按 URL 累加，`:42` 又用 `owner or state.get('owner')` 保留旧 owner。
- **部分缓解（如实说明）**：`integrations/live_browser.py:210-212` 与 `:234-236` 在 `import_following()` 内另有 owner 一致性检查，但仅在 `profile.sec_uid` 已捕获且新账号响应带 owner 时生效；`profile.sec_uid` 未捕获或响应无 owner 参数时两条检查均被跳过。本轮**未在真实登录态下复现完整 UI 链路**（见第 6 节），故标注为条件性缺陷，不声称已在用户界面上看到该现象。
- **影响**：数据污染、隐私混淆、批量导入错误主播、数量自相矛盾。
- **绕过**：换号后等待 >15 分钟让 `stale` 触发重置，或退出重开专用浏览器后重新登录。
- **修复建议**：以登录态标识（cookie/session 指纹或 `profile.sec_uid`）作为快照命名空间；账号标识变化时无条件清空 `live_following`；`observe()` 在 `owner` 缺失且不能确认与当前账号一致时拒绝合并并置 `integrity='unconfirmed'`；导入前校验 `loaded_count<=expected_count`。
- **验收条件**：带/不带 owner 参数的换号场景、`profile.sec_uid` 有/无的换号场景共 4 组，切换后 `users` 只含新账号数据且 `loaded_count<=expected_count`；连续两次导入同一账号不重复累计；中断续读不丢已读页。

### VD27-003 部分关注列表未明确展示「未读取差额」

- **严重性**：低。不误报完整、不丢数据，仅信息披露不完整；有绕过（用户自行相减）。
- **构建/环境**：同 VD27-002，UTC+8 2026-11-14 15:00 / 15:16。
- **输入**：`first_seen=true, has_more=true, total=500, users=120`。
- **最短复现**：`python repro/scripts/source_repro.py` → `A3_partial_list_message`。
- **预期**：文案明确给出「已读取 / 已知总数 / 未读取差额 / 完整性未确认」四项，且不把平台总数当作可读取条目数。
- **实际**：`已读取 120 位关注，账号显示共 500 位；尚未确认完整，请继续加载。平台隐藏、失效账号也可能造成差额` —— 已读取 ✅、已知总数 ✅、完整性未确认 ✅，**未读取差额 380 缺失**，且 `complete=false` 正确（未宣称完整）。
- **复现次数**：**2/2**。
- **证据**：`repro/evidence/source_repro_run1.json`、`source_repro_run2.json`。
- **已证实根因**：`integrations/following.py:53-57` 的 `summary()` 只拼接 `count` 与固定提示，未计算 `expected-len(users)`；调用路径 `/api/live/following` → `live_browser.import_following` → `following.summary` → `static/media26.js:61`。
- **影响**：用户无法一眼判断还差多少没读完，容易误以为剩余可继续加载就能补齐平台隐藏/失效条目。
- **绕过**：人工用 500−120 计算。
- **修复建议**：`summary()` 增加 `unread_count`、`missing_count` 字段，文案显式写出差额并说明「平台总数含不可见/失效条目，不保证可全部读取」。
- **验收条件**：`total>loaded` 时响应含数值差额且界面可见；`total<loaded` 时不得显示负数差额、不得标记 `complete=true`；`total=None` 时只显示已读取与「完整性未确认」。

### VD27-004 更改顶部素材保存目录后，直播录播仍写入旧目录

- **严重性**：中高。用户迁移/腾空旧目录后，新录播仍写入旧路径，回放「找不到」或写到空间不足的卷；界面明确写「保存位置沿用顶部设置」，属界面误导。**不阻断录制，有可靠绕过**。
- **构建/环境**：EXE `676a999a…d2d`；UTC+8 2026-11-14 15:18；实例 `127.0.0.1:2746`；输出目录全部在 `repro/qa/output/` 下。
- **输入**：两轮独立路径 A→B、C→D，均在 QA 目录内；`POST /api/live/settings` 时 `output=''`。
- **最短复现**：`POST /api/settings {output:A}` → `POST /api/live/settings {output:''}` → `POST /api/settings {output:B}` → `GET /api/live` 与 `GET /api/state`；再 `POST /api/live/anchors/{id}/start` 看任务目录。
- **预期**：直播输出跟随顶部设置变为 B；新录播任务目录位于 B。
- **实际**：`/api/state` 全局=B，`/api/live` 仍=A；两轮均如此。新任务目录分别落在 `…\qa_global_A\20261114-151858_…` 与 `…\qa_global_C\20261114-151859_…`（旧目录），`GET /api/state` 的 `job.directory` 可验证。
- **复现次数**：**2/2**（设置分歧 2/2、任务落盘 2/2）。
- **证据**：`repro/evidence/output_divergence.json`、`repro/evidence/matrix_raw.json`（B-out-*）、`TEST-MATRIX.csv` B-001 / B-002。
- **已证实根因**：`integrations/live.py:323` 把 `output` 物化为绝对路径并 `:324` 持久化到 `live-settings.json`；`:172` 建任务时使用 `self.settings['output']`。前端 `static/app.js:86` 的 `saveDirectory()` 只调用 `/api/settings`，不联动 `/api/live/settings`；只有 `static/media26.js:43-45` 的 `saveLiveSettings()`（需用户另点「保存」）才会同步。调用路径：顶部保存 → `/api/settings` → `SETTINGS`；录播 → `Manager.start` → `new_job`。
- **影响**：素材落盘位置与用户认知不符，可能写入已满/已迁移的卷；定时/自动监控在无人干预时持续写旧目录。
- **绕过**：改完顶部目录后，再到直播面板点一次「保存」设置。
- **修复建议**：`/api/live/settings` 允许 `output=''` 表示「跟随顶部设置」并持久化为哨兵而非绝对路径；或 `Manager.start()` 在 `live.output` 为空时读取 `a.SETTINGS['output']`；`/api/settings` 变更时同步通知 LIVE 管理器。
- **验收条件**：改顶部目录后 `GET /api/live` 的 output（或 `follow_global=true`）与 `/api/state` 一致；新建录播任务目录位于新目录；显式为直播单独指定目录时仍优先使用单独值；重启后行为一致。

### VD27-005 房间数据缺少 `status` 时被断言为「已开播」

- **严重性**：高。把「无法确认状态」误报为「已开播」，会进入录制分支、拉取可能失效的流地址；与需求「不能把无法确认状态误报为未开播或已开播」直接冲突。
- **构建/环境**：同 VD27-002，UTC+8 2026-11-14 15:00 / 15:16。
- **输入**：合成房间 JSON，含 `stream_url.flv_pull_url.origin`、`owner.nickname`、`id_str=7381928374650910213`，**不含 `status` 字段**；对照组 `status=4`。
- **最短复现**：`python repro/scripts/source_repro.py` → `B2_missing_status_claimed_online`。
- **预期**：状态未知时不返回 `online=True`（返回 `online=None`/`unknown`，由上层提示人工确认）。
- **实际**：`room_data()` 返回 `{'online': True, 'url': …, 'room_id': '7381928374650910213'}`；对照组 `status=4` 正确返回 `online=False`。
- **复现次数**：**2/2**。
- **证据**：`repro/evidence/source_repro_run1.json`、`source_repro_run2.json`。
- **已证实根因**：`integrations/live_browser.py:77` `str(obj.get('status', 2)) == '2'` —— 缺字段时默认值 `2` 即「开播」。调用路径 `resolve()` → `room_data()` → `Manager.record()`（`integrations/live.py:229,242`）→ `capture_segment()`。
- **影响**：登录失效/限流/裁剪的 RSC 数据下误判开播、启动无效录制、产生空/截断片段或误报状态。
- **绕过**：人工在专用浏览器确认直播能播放后再录制；或改用手机检测。
- **修复建议**：`status` 缺失/非 2/4 时返回 `online=None, state='unknown'`，上层 `record()` 与 UI 显示「无法确认开播状态」并停止自动录制；只有显式 `status==2` 才置 online。
- **验收条件**：`status` 缺失、为 `null`、为字符串 `"2"`、为 `4`、为 2 的 5 组输入分别得到 unknown / unknown / online / offline / online；`record()` 在 unknown 时不得调用 `capture_segment`，任务消息含「无法确认」。

### VD27-006 云服务 API 密钥无法通过界面撤销

- **严重性**：中。凭据长期驻留，用户无法撤销/轮换；不使用他人付费密钥测试，本轮只用假值。**不阻断功能，但属凭据保护缺陷，绕过需手工改文件**。
- **构建/环境**：EXE `676a999a…d2d`；UTC+8 2026-11-14 15:18；实例 `127.0.0.1:2746`；假密钥 `<test-dummy-key>/2`（非真实凭据）。
- **最短复现**：`POST /api/translation/services` 写入假 `asr.api_key` → 再 POST 同一服务的 `api_key: ""` → `GET /api/translation/services`。
- **预期**：清空后 `has_key=false`，持久化文件中不再保留该密钥。
- **实际**：两轮清空后 `has_key` 均为 `true`（`api_key` 回显始终为空，因此用户看不出密钥仍在）：查询接口只回显 `has_key`，空值被静默回填为旧密钥。
- **复现次数**：**2/2**。
- **证据**：`repro/evidence/credential_clear.json`、`repro/evidence/matrix_raw.json`（D-services-*）。
- **已证实根因**：`integrations/translation.py:414` `if not value['api_key']: value['api_key'] = previous.get(key, {}).get('api_key','')`；`:408` 的查询接口以 `has_key` 表示存在。`POST /api/translation/services` → `save_services()` → `pipeline.services_path`。
- **影响**：误填/泄露的密钥无法从界面撤销，只能手工编辑数据文件；与「凭据保护」要求不符。
- **绕过**：手工删除/编辑隔离数据目录中的服务配置文件（不应要求终端用户这样做）。
- **修复建议**：协议增加显式 `clear_api_key: bool` 或使用 `null` 表示「清除」、`""` 表示「保持不变」；查询接口返回可区分状态；清除后写回并校验文件。
- **验收条件**：显式清除后 `has_key=false` 且持久化文件不含该值；未提交 `api_key` 时旧值保持不变；响应与诊断日志/导出均不回显真实密钥。

---

## 2. 已验证项目（PASS 摘要，共 44 项）

- **启动与隔离**：3 次 `--self-test --home/--report/--stop-file` 均成功写报告并启动服务；`installation_id` 与 `server.json` 一致；输出目录确认指向 QA；正常 10 分钟超时退出 0（非崩溃）；第二次启动在并发负载下出现 `browser` 组件检查 45 s 超时 → 启动器自动核对文件完整性并降级启动（`degraded=true`、明确列出未就绪组件），账号/设置/主播列表保留，第三次启动恢复 `degraded=false`。
- **A 组**：备注留空可添加、备注可编辑、同名不同房间不合并、非法链接/非抖音链接/已发布视频链接/缺房间号均有 4xx 中文原因、含中文文案与首尾空格的分享链接可解析、19 位 room ID 逐字符保留（JS 数值会变成 `7.381928374650911e+18`，产品后端未丢失）。
- **B 组**：并发 0/9、分段 0/121 正确拒绝，并发 1/8 接受；跨午夜定时窗口 4/4 边界正确；未登录直播间启动后任务为 `error`、消息「已停止读取直播间…可改用手机检测…」、`replay=false`，**未误报「未开播」或「成功」**；坏文件不伪报成功。
- **C 组（PC 侧）**：`/api/phone` 正确列出 vivo V2304A 且 `connected=false`；桥接缺 key/错 key 均 401；合成 online 事件生成 pending、5 分钟内重复 online 标记 duplicate、offline 事件、忽略与重复点击幂等；无效状态/目标/主播/事件分别 400/404；路径穿越 4/4 返回 404。
- **D 组**：缺本地组件时 `/api/translation` 400 且不静默上云；云服务+背景声缺分离组件时 409 且给出可关闭选项；`/api/translation/services` 不回显 api_key；非法 `base_url` 400。
- **E 组**：`/api/hardware` 真实试编码区分「支持/已选择/实际可用」，正确选定 `h264_nvenc`。
- **F 组**：合成视频抽帧 done 且产出 6 个文件；清理预览只列可确认归属且未修改的生成副本；清理执行后 3 个任务目录内哨兵哈希不变、被修改文件保留、`deleted=6` 与实际移走 6 个文件一致；顶层 7 个哨兵全部未变；零字节→400「视频文件为空」，损坏媒体→任务 error 且给出 ffmpeg 原因，两者不影响另一任务正常完成；中文/空格/逗号文件名可用；QA 实例诊断日志不含 Cookie:/Authorization:/session_token/测试假密钥/配对密钥字面量；重启后设置与主播列表保留。
- **源码一致性**：11/11 文件与 EXE 内 payload 同哈希。

## 3. 失败用例与 Bug 对照

| 用例 | 状态 | Bug | 说明 |
| --- | --- | --- | --- |
| A-001 | FAIL | VD27-001 | 6 个同房间合法链接保留 4 条 |
| A-002 | FAIL | VD27-001 | 反序、换房间号复现 2 条 |
| A-108 | FAIL | VD27-002 | 换号混入 alice/bob |
| A-109 | FAIL | VD27-003 | 未给出 380 的未读取差额 |
| B-001 | FAIL | VD27-004 | 全局与直播 output 两轮均不一致 |
| B-002 | FAIL | VD27-004 | 新任务两轮均落旧目录 |
| B-004 | FAIL | VD27-005 | 缺 status 返回 online=True |
| D-004 | FAIL | VD27-006 | 两轮清空后 has_key 仍为 true |

合计 **8 个 FAIL 用例 → 6 个 Bug**（VD27-001、VD27-004 各由 2 个用例同根因合并）。CSV 共 **70** 行：`PASS 44 / FAIL 8 / BLOCKED 17 / SKIPPED 1`，与 Bug 编号映射一致。
另有 2 个**非产品缺陷的测试环境问题**（不计入 FAIL）：见第 6 节源码测试挂起与首次依赖缺失。

## 4. 代码审查候选（只读发现，未复现，不计入 Bug）

1. **`integrations/live.py:195-197` 凭据进入子进程命令行**：`capture_segment()` 把 `Cookie:` 头拼进 FFmpeg `-headers` 参数。代码注释称「headers 不进入持久化任务/证据/命令日志」，但 Windows 进程命令行可被本机其他进程读取。本轮未在真实录制中验证泄露，仅列为候选；建议改用 `-headers` 之外的临时配置文件或 stdin 传递。
2. **`integrations/recycle.py:29-42` 回收站返回 HRESULT 80070002 仍计为删除成功**：F-003 实测 6 个文件中有 2 个产生 `系统回收站报告异常（HRESULT 80070002）；原路径已移走，请核对回收站` 警告，`deleted=6`、`errors=[]` 且 `materials_deleted=true`。文件确实离开原路径，计数与实际一致，故本轮判 PASS；但「已移走」不等于「已进入回收站」，建议核验回收站项后再计成功。
3. **`integrations/mobile.py:160-167` 任意已认证桥接请求刷新 `last_seen`**：`handle_request()` 对每个带正确 key 的请求都更新时间戳，`connected` 因此表示「桥接有流量」而非「手机伴侣在前台」。本轮用合成请求使 `connected=true`，属设计取舍，未判定为缺陷。
4. **`integrations/live.py:242` `if not info.get('online')`**：`online=None`（若按 VD27-005 修复引入）会被当作「未开播」。修复 VD27-005 时必须同时改此处，否则引入回归。

## 5. 未测试项目（BLOCKED / SKIPPED，各用例原因见 TEST-MATRIX.csv）

- **BLOCKED 17 项**：A 组真实账号关注数量/多页/中断续读/部分页失败/登录失效/换号/已删除账号的真实导入（2 项）；B 组真实开播全流程、多主播并行与排队、定时/跨午夜/分段/磁盘不足/不可写/重启恢复、403/429/444 与安全验证（4 项）；C 组 APK 覆盖安装与 ADB reverse、前台轮询旧回调、系统录屏与跳转原生抖音及三种停止入口、`Movies/VideoDesk` 音画与实听、抖音通知入口（5 项）；D 组识别→翻译→配音成片全流程、字幕版式与配音质量、编辑重合成与批处理隔离、边界媒体与断网（4 项）；E 组 CPU/GPU 有效运行对比（1 项）；F 组升级/修复破坏性测试（1 项）。
- **SKIPPED 1 项**：E-003「独立进程模拟显卡不可用/显存不足并验证 CPU 回退」，本轮不改动用户驱动、不伪造硬件结果，主动跳过。
- **DSH 集成测试**：`node --test tests/test_dsh_adapter.mjs` 退出码 0，但 **2/2 用例 SKIP**（未设置 `VIDEO_DESK_URL`/`VIDEO_DESK_TEST_VIDEO`/`VIDEO_DESK_TEST_OUTPUT`）。原始输出 `repro/logs/source_dsh_test.log`。按要求披露，未伪装成通过。

## 6. 环境阻碍（含「没有登录账号」的核对结论）

1. **无抖音登录态 / 平台页面打不开 —— 确认为 A、B、C 真实链路的唯一阻碍**。隔离实例浏览器 profile 为空，未进行人工登录，也未绕过验证。因此「关注列表导入」「真实直播间录制/回放」「手机真机联动」全部写 BLOCKED，**没有填 PASS**。用户提出的「可能是没有登录账号无法打开」与这一部分的观察一致。
2. **但「没有登录账号」无法解释本轮 6 个缺陷，也无法解释下面两处本机现象**：
   - `browser` 组件检查超时：`integrations/runtime_probe.py:36-40` 的探针只做 **headless Chromium 启动/关闭**，不打开任何平台页面、不需要账号、不联网。当时并发运行源码单测、可用内存仅 3.99 GB / 15.95 GB（74% 占用），45 s 超时后启动器自动核对并降级启动。属资源竞争，与登录无关。
   - 4 个源码测试模块挂起：`tests/test_pipeline.py` 文件头明确写「End-to-end local file tests. No social-platform account or network is used」，使用本地回环 HTTP 服务与本地 FFmpeg；`test_login_browser` 卡住的用例也是本机 `127.0.0.1` 假下载服务。设置 `no_proxy=127.0.0.1,localhost,::1` 后仍挂起，排除代理为唯一原因；直接调用 `youtube_support()`（本地 Node JS 运行时探测，不联网）也超过 45 s 未返回。判定为**测试环境/子进程启动挂起**，不写成产品缺陷。
3. **源码测试退出码与用例数（原始输出保留）**：
   - `python -m unittest discover -s tests -p "test_*.py"`（测试 shell Python 3.13.14）：退出码 **1**，收集 37 个（含 17 个 `_FailedTest` 占位），2 failures + 20 errors；全部为 `ModuleNotFoundError: fastapi/imagehash/yt_dlp/imageio_ffmpeg`，属**依赖缺失**，不是产品缺陷。原始输出 `repro/logs/source_unittest.log`。
   - 改用**发布包内置 Python 3.13.17**（依赖齐全）重跑完整 discover：**挂起超过 8 分钟无输出**，已主动终止，无有效退出码。原始记录 `repro/logs/source_unittest_bundled.log`（0 字节）与任务记录。
   - 改逐模块运行：13 个模块 **144 个用例全部 OK、退出码 0**；`test_image_dedup`、`test_login_browser`、`test_pipeline`、`test_video_sources` 4 个模块 **各 120 s 超时（rc=124）**，日志见 `repro/logs/per_module/`。与历史验收「175 tests / 35.86 s / 0 failure」差异**不作为本轮 PASS 或 FAIL 结论**，只作为环境阻碍披露。
   - `node --test tests/test_dedup_ui.mjs tests/test_progress_ui.mjs tests/test_sheet_limit.mjs tests/test_phone_detector.mjs`：退出码 **0**，收集 **21** 个用例，21 pass / 0 fail / 0 skipped。原始输出 `repro/logs/source_nodetest.log`。（注：`docs/BUILD.md:61` 只列 3 个文件，本轮按任务要求补测 `test_phone_detector.mjs`。）
   - 未批量运行 `scripts/verify_*.py`：按提示，部分脚本依赖特定构建或会写入测试数据，避免污染。
4. **RAM 查询受限**：沙箱拒绝 CIM/WMI 与 `systeminfo`，内存改用 `kernel32.GlobalMemoryStatusEx` 取得；不影响测试结论。
5. **手机只有一台且已配对**：`adb reverse`、APK 覆盖安装会影响用户当前配对；仅做只读查询（`getprop`、`pm path`/`sha256sum`、`dumpsys media_projection` 无活动投影），未清数据、未卸载、未安装、未 reverse、未读取通知、未录屏、未跳转抖音。系统确认类操作未获人工授权，未填 PASS。
6. **AI 组件未安装**：隔离 home 的 CPU/GPU 运行时与 4 个模型均为 false，安装需数 GB 下载；因此 D 组成片、E 组速度对比无法完成。未使用任何他人付费密钥。

## 7. 已知限制（先验证说明是否准确）

| 限制 | 本轮验证 |
| --- | --- |
| 手机每次新建录屏会话可能要求本人授权；后台通知不能代用户同意 | 未获授权，未验证；仅确认 `dumpsys media_projection` 当时无活动投影 |
| 单台手机一次录一路屏幕，录制时暂停切换监测；多主播并行由电脑完成 | 源码 `mobile/src/.../MainActivity.java` 与 `RecordingService` 存在单会话设计；真机行为未验证 |
| 手机网页轮询需保持前台，vivo 省电策略可能冻结后台 | 未验证（需人工前台操作） |
| 电脑通信依赖 USB + ADB reverse，不承诺拔线后联网控制 | 结构与端口绑定符合说明；未做断线测试 |
| 手机录制保存在手机本地、不自动上传；声音受 Android 采集规则影响 | 未验证（未录制） |
| 昵称只是备注；平台关注总数含不可见/失效条目，工具必须如实显示完整性 | **部分验证**：`summary()` 的 `complete=false` 与「尚未确认完整」文案正确，未把部分列表宣称完整；但未给出数值差额（VD27-003） |
| 真实平台可能要求人工登录/验证/限流 | **确认**：本机隔离实例无登录态，构成本轮最大阻碍 |

**限制不能解释的现象**：VD27-001/002/004/005/006 均为本地逻辑/状态问题，在合成输入下稳定复现，与平台限流、未开播、无登录态无关；不属「已知限制」。

## 8. 复现命令

所有命令在 `E:\VideoDesk` 执行；路径均为绝对路径或仓库相对路径。先运行 `repro/README.md` 中的顺序。

```bash
# 0) 计算目标哈希（应完全一致）
sha256sum "E:/VideoDesk/release/VideoDesk-2.7.0-Windows-x64.exe" \
          "E:/VideoDesk/release/VideoDesk-Android-1.0.0.apk"

# 1) 建立隔离数据与保护哨兵
python "E:/VideoDesk/repro/scripts/make_sentinels.py"
python "E:/VideoDesk/repro/scripts/make_media.py"
python -c "import json,pathlib; p=pathlib.Path(r'E:\VideoDesk\repro\qa\home\data\settings.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps({'output':r'E:\VideoDesk\repro\qa\output'}),encoding='utf-8')"

# 2) 启动隔离实例（先清自己的停止标记；约 10 分钟正常超时退出，不算崩溃）
rm -f "E:/VideoDesk/repro/qa/stop/stop.flag"
"E:/VideoDesk/release/VideoDesk-2.7.0-Windows-x64.exe" --self-test \
  --home "E:/VideoDesk/repro/qa/home" \
  --report "E:/VideoDesk/repro/qa/self-test-report.json" \
  --stop-file "E:/VideoDesk/repro/qa/stop/stop.flag"
# 从 E:/VideoDesk/repro/qa/home/data/server.json 读取本次真实端口（不要固定端口）

# 3) 黑盒回归（自动读取 server.json；生成 TEST-MATRIX.csv 与证据）
python "E:/VideoDesk/repro/scripts/run_matrix.py"
python "E:/VideoDesk/repro/scripts/probe.py"
python "E:/VideoDesk/repro/scripts/phone_bridge_probe.py"

# 4) 源码级确定性复现（必须用发布包内置 Python；路径按实际版本目录）
PY="E:/VideoDesk/repro/qa/home/versions/2.7.0-e5df71c0b1/runtime/main/python.exe"
VIDEO_DESK_DATA="E:/VideoDesk/repro/qa/source-tests-data" \
VIDEO_DESK_DEFAULT_OUTPUT="E:/VideoDesk/repro/qa/source-tests-output" \
  "$PY" "E:/VideoDesk/repro/scripts/source_repro.py"

# 5) 源码测试（原始输出见 repro/logs/）
python -m unittest discover -s tests -p "test_*.py"
node --test tests/test_dedup_ui.mjs tests/test_progress_ui.mjs tests/test_sheet_limit.mjs tests/test_phone_detector.mjs
node --test tests/test_dsh_adapter.mjs     # 未设三个环境变量时 2/2 SKIP

# 6) 收尾：哨兵复核 + 脱敏扫描 + 停止自己的实例
python "E:/VideoDesk/repro/scripts/verify_sentinels.py"
python "E:/VideoDesk/repro/scripts/scan_secrets.py"
# 创建停止标记让 self-test 正常结束（不要停止用户原有服务）
type nul > "E:\VideoDesk\repro\qa\stop\stop.flag"   # CMD
# 或 PowerShell: New-Item -ItemType File "E:\VideoDesk\repro\qa\stop\stop.flag" -Force
```

**停止方法**：向 `--stop-file` 指定的路径创建标记文件；self-test 检测到后结束。三次启动均已自然超时退出。未触及用户正在运行的工作台、正式任务库、桌面入口或手机配对。

## 9. 修复优先级

| 优先级 | Bug | 理由 |
| --- | --- | --- |
| **P0** | VD27-002 | 跨账号数据混淆/隐私，可能批量导入错误主播；修复后需补 4 组换号回归 |
| **P0** | VD27-005 | 状态未知误报开播，直接影响 B 组录播正确性；与 VD27-004 的回归点耦合（需同步改 `live.py:242`） |
| **P1** | VD27-004 | 界面说明与实际落盘不符，自动监控长期静默写错目录，2/2 稳定复现 |
| **P1** | VD27-001 | 主播身份去重，影响 A 组最高优先级链路，2/2 稳定复现 |
| **P2** | VD27-006 | 凭据撤销能力，合规与凭据保护 |
| **P3** | VD27-003 | 信息展示缺口，不影响正确性 |

建议修复顺序：先 VD27-005（含 `live.py:242` 联动）与 VD27-002，再 VD27-004/001，最后 006/003。每个修复需用本报告第 1 节的「验收条件」重新跑 `repro/scripts/run_matrix.py` 与 `source_repro.py`。

## 10. 测试结束状态

- 三个自启 `--self-test` 实例均已在约 10 分钟正常超时退出（或收到停止标记），**未留下运行中的测试服务**；用户原有实例/工作台/托盘未被启动、停止或改写。
- 隔离实例的 `settings.json`、`phone-settings.json`、`translation/services` 均为**我自己在 `repro/qa/home` 内创建/修改的测试配置**，保留作为证据；未改动用户 `AppData\Local\VideoDesk\data` 下任何配置。
- 未创建/修改用户「文档\映格素材」内容；所有生成/清理/回收都发生在 `E:\VideoDesk\repro\qa\`。清理测试只对 QA 任务目录内**已登记的生成副本**执行回收，3 个任务目录内哨兵与顶层 7 个哨兵哈希全部不变。
- 交付物中不含 Cookie、Authorization、session token、浏览器资料、桥接配对密钥、真实 API 密钥、keystore 或签名密码；手机序列号与用户目录已脱敏。脱敏扫描方式与结果见 `repro/evidence/secrets_scan.json`，并已人工检查无截图/媒体文件（本轮未采集截图、录像或含个人信息的平台页面）。
- 本轮**未修改产品代码、未重建发布包、未发布、未替换用户桌面软件、未清理用户文件**。
- **结论**：在已执行的 70 个用例范围内发现 **6 个可复现缺陷**（8 个 FAIL 用例）。未执行/无法确认的范围见第 5、6 节，**不声称软件没有其它 Bug**。
