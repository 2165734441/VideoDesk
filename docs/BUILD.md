# 从源码运行与构建

源码版面向 Windows 10 / 11 x64；依赖与网络组件以发布时锁定的版本为准。

## 运行

1. 安装 Python 3.12、Git、Node.js 22，以及 Edge 或 Chrome。
2. 下载仓库源码并解压到可写目录，或运行 `git clone https://github.com/2165734441/VideoDesk.git`。
3. 双击 `install.cmd` 安装依赖，再双击 `start.cmd` 打开工作台。安装需要联网。
4. 默认地址为 `http://127.0.0.1:18966`。完成任务后运行 `stop.cmd` 关闭源码版后台。

MediaCrawler 使用独立 Python 3.12 环境，且限非商业学习研究。其源码由安装脚本按固定提交下载，不随此仓库分发。可显式指定 Python：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -CrawlerPython 'C:\Python312\python.exe'
```

源码仓库不包含 `runtime-tools/ffprobe.exe`。安装时通过 `scripts/prepare_merge.py` 下载并校验固定版本工具，即使源码里已有来源清单也会补齐缺失的可执行文件。

更换端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Port 18967
```

首次使用本地语音识别，可在“账号与模型”安装 SenseVoice，或导入同一模型的离线包。模型及识别组件的许可与项目代码分别保留。

## 命令行与 DSH

启动工作台后，可运行：

```powershell
node bin/video-desk.mjs help
node bin/video-desk.mjs storyboard "D:\视频\课程.mp4" --duration 60
node bin/video-desk.mjs batch --folder "D:\视频" --outputs frames --segment 10
```

DSH 安装说明见 [dsh-plugin/README.md](../dsh-plugin/README.md)。网页、CLI 和 DSH 插件共用本机服务与任务库。

## 构建 Windows 单文件

构建前先完成上述安装，保留安装脚本创建的 MediaCrawler Git 仓库与固定提交。还需要 Visual Studio / Build Tools 的 .NET Framework C# 编译器，以及经授权可再分发、签名有效的 VC x64 CRT 文件。构建脚本会校验来源与签名。

```powershell
python scripts/build_windows_exe.py --help
python scripts/build_windows_exe.py all
```

构建会下载运行时、浏览器及依赖，可能占用数 GB。构建产物位于 `release/`。成品包含第三方二进制、模型或采集组件时，发布者还需要整理对应许可证和必要的对应源码；源码仓库的 Apache-2.0 授权不替代它们的分发要求。

生成不含运行时二进制和私有资料的源码安装包：

```powershell
python scripts/package_share.py
```

## 验证

```powershell
python -m unittest discover -s tests -p "test_*.py"
node --test tests/test_dedup_ui.mjs tests/test_progress_ui.mjs tests/test_sheet_limit.mjs
```

DSH 集成测试 `tests/test_dsh_adapter.mjs` 需要显式设置 `VIDEO_DESK_URL`（隔离测试服务）、`VIDEO_DESK_TEST_VIDEO`（测试视频的完整路径）和 `VIDEO_DESK_TEST_OUTPUT`（测试输出目录）；缺少其中任何一项时自动跳过。请在隔离数据目录启动测试服务，避免写入日常任务库。

既有 2.5.0 验证范围、结果和限制见 [WORKFLOWS25-TEST-REPORT.md](../WORKFLOWS25-TEST-REPORT.md)。需要真实视频或本机应用状态的集成脚本不能代替首次安装验证。
