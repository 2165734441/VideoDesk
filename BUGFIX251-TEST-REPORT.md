# VideoDesk 2.5.1：独立 Bug 报告修复与复测记录

日期：2026-11-13。输入为用户提供的 `BUG-REPORT.md`、`TEST-MATRIX.csv` 和 `repro/`。原报告的 94 条用例统计仍为 PASS 63 / FAIL 11 / BLOCKED 2 / SKIPPED 18；本记录是修复后的定向回归，**没有把原来的全部 94 条用例重新标为通过**。原报告、矩阵和复现资料均未修改。

## 修复内容与验收

| 编号 | 修改 | 本次验证 |
| --- | --- | --- |
| VD25-01 / P0 | `material_history.py` 不再把目录扫描结果当作生成归属；由实际写入者逐项登记。批处理先在私有临时目录计算哈希，再发布并核对。重新生成覆盖同样拒绝新目录里的未登记/已修改文件。 | 在任务运行时放入 `MY-NOTE-DO-NOT-DELETE.txt`，500 张生成图片进入清理清单，便签被跳过；实际回收图片后，便签内容和 SHA256 不变。源码服务和最终 EXE 均通过。单元测试另外验证旧目录记录、用户改动、发布时被替换、新生成目录中的便签。 |
| VD25-02 / P1 | 即使 FFmpeg 退出码为 0，也检查解码损坏、截断等诊断；标记 `incomplete_media` 并报错，避免把部分解码当作完整成功。旧抽帧入口与远程流也检查诊断，远程流仅保留通用诊断，不写入带签名的地址或请求头。 | 报告中的截断样例在 3 秒和 5 秒间隔下均报错；健康样例仍分别生成 7 / 4 张。源码与 EXE 均通过。 |
| VD25-03 / P1 | 父批次区分成功、失败、取消；部分成功仍可完成已产生资料的汇总，状态不再统一变为 done；进度按成功项计算。 | 1 成功 + 1 失败 → error、50%；1 成功 + 1 取消 → cancelled、50%。源码与 EXE 均通过。 |
| VD25-04 / P1 | 启动器在访问 .NET 的环境字典、启动 CLI 或子进程前，通过 Windows 原生环境块去除名称大小写重复。只修改自己的环境；子进程仍清除外部 Python 环境干扰。 | 原生重复环境块测试通过；最终 EXE 使用 HTTP_PROXY/http_proxy 与 HTTPS_PROXY/https_proxy 重名环境启动，独立安装、CLI、再次启动均通过。PATH 仅系统目录，PYTHONHOME/PYTHONPATH 指向不存在位置。 |
| VD25-05 / P1 | 回收操作报告异常后复核原路径，区别“仍在原处”和“原路径已移走”；后者计入移走数量并提示核对回收站，不再谎报文件保留。没有永久删除回退。 | 真实 Windows 回收 500 张图片成功；真实占用文件回收失败，文件仍保留且返回准确错误；单元故障注入覆盖“移走后返回 HRESULT 错误”，计数和警告符合实际。 |
| VD25-06 / P2 | 清理数量、跳过项、错误和警告按任务分别记录，共享路径去重保留所属任务映射；无可验证归属、删除 0 项的任务不会被标为已清理。 | 最终 EXE 同时清理两个各有 4 张图片的任务，第二个任务锁定 1 张：全局回收 7 项；第一任务 4 项、0 错误、已清理，第二任务 3 项、1 错误、未清理；锁定文件仍在。单元测试覆盖无归属和 0 项情况。 |
| VD25-07 / P2 | 字幕截图页面新增独立起始秒数和处理时长，提交 `analysis.start/duration` 并保存页面设置。 | 真实 Chromium 点击提交，10 秒起始、5 秒时长 → 实际处理 10–15 秒；刷新后仍为 10 / 5；无页面 JS 错误。源码与 EXE 均通过。 |

## 验证结果

- Python 测试：127 项通过。包含 11 项新增报告回归；原生重复环境变量用例实际编译运行 C# 测试程序。
- Node 前端测试：6 项通过。
- 最终 EXE 自检与基础操作：首次独立释放、第二次复用、CLI help、本地 HTTP 下载及抽帧、直连抽帧、音轨、原片时间索引、图片相似去重、导出/隔离/恢复通过。
- 最终 EXE 定向回归：上述合成视频、混合批次、真实回收站、真实文件占用、字幕页面提交及刷新均通过；包内运行源码 `app.py` 与本次工作区源码逐字节一致。
- 最终 EXE 运行时 DSH 集成：2 项通过，连接本次隔离服务并使用合成视频。
- 输入合成样例处理前后哈希相同。所有清理仅针对本次隔离目录生成的测试文件。

首次 EXE 下载测试使用不可连接的合成代理地址，启动与 CLI 已成功，下载被该代理阻断。后续使用同名大小写重复、值为空的代理变量完成全部媒体测试；保留了首次失败日志。这不代表验证了任意代理服务器的可用性。

## 文件与证据

本地证据（不进入公开源码包）：

- `.local/bugfix25/unit-final.log`：127 项 Python。
- `.local/bugfix25/node.log`：6 项 Node。
- `.local/bugfix25/live-20261113-142629/results.json`：源码服务定向回归，包括重新生成覆盖与便签保护。
- `.local/bugfix25/exe-verification.json`：最终 EXE 安装、复用及基础操作。
- `.local/bugfix25/live-20261113-143204/results.json`：最终 EXE 定向回归、重新生成覆盖、文件占用及 DSH。
- 同目录 `cleanup.json`、任务 JSON、`subtitle-range.png` 和 `dsh.log` 为细项证据；服务数据目录含运行时会话资料，不能整体公开。

最终 EXE：`release/VideoDesk-2.5.1-Windows-x64.exe`，749,249,536 字节。

SHA256：`7189cdb37b97b7558715e69703d4f8f8b0d04bab0c5f0e97dcc6d09b25cdb0cd`。

载荷 SHA256：`a0a962748569e944a086aaa26d709ad6c89413a3f6eab1c6b6f961a3025e6756`。

使用的报告样例：

| 样例 | SHA256 |
| --- | --- |
| main_20s.mp4 | `0d9da46b41996dbd4fdfd3aafb5f85660b58769622a8106886fdb47fa5d76f5d` |
| truncated.mp4 | `1266bfc13c959c54a01942f2027d379101cb3fd04356e07392773a23225888c2` |
| audio_only_as_mp4.mp4 | `9dc19d2647e89c0ce5be02e8151462692348e96d278c459c96a6df0a92784cbf` |

## 升级与限制

2.5.0 的旧目录快照无法区分用户加入的文件与软件生成文件，**不能自动转为可信归属**。升级后保守保留这些旧素材，需要用户核对后手动整理；2.5.1 新任务使用明确的生成归属记录。清理保护原片、用户改动、未登记文件、被其他任务引用的资料及链接路径。

视频预检仍以容器元数据与流信息为主，并非完整解码验收；实际处理会检查发现的解码异常。没有声称识别一切损坏格式、或将达到主动截图上限视为损坏。

本次没有补齐原报告所有未测项：真实平台账号和验证码、MediaCrawler 采集、网页录制、网页 File 断线/重选、四档完整性能对比、AV1、目录联接权限及双向跨盘等仍须独立复测。此前的历史记录和这次通过项不能替代这些验证。

## 复测命令

在具有依赖的 Python 环境、项目根目录执行；显式使用自己的隔离输出，勿连接日常服务。合成样例来自本次交接包中的原始 `repro/samples`。

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:VIDEO_DESK_DATA=Join-Path $PWD ('.local/bugfix25/unit-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_dedup_ui.mjs tests/test_sheet_limit.mjs tests/test_progress_ui.mjs
python scripts/verify_bugfix25.py --samples '<合成样例目录>'
python scripts/verify_windows_exe.py --exe '<2.5.1 EXE>' --duplicate-proxies --report-output '<独立验证报告.json>'
python scripts/verify_bugfix25.py --samples '<合成样例目录>' --exe '<2.5.1 EXE>'
```

`verify_bugfix25.py` 将副本、测试数据和证据写入独立 `.local/bugfix25/live-时间/`，结束时仅关闭自己启动的服务。Windows 清理验证会将本次生成的测试图片移入系统回收站，并保留注入的便签与锁定文件。最终 EXE 的安装测试仍须在普通用户自己的隔离目录中独立复核。
