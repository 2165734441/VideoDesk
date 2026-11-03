映格 Video Desk 2.5.0 —— 源码分享包

这是 Windows 源码安装包，不是免安装 EXE，也不是已经部署好的公网网站。
请先解压整个压缩包，再运行里面的文件，不要直接在压缩包预览中运行。

首次使用：
1. 安装 Python 3.12（包含 Python Launcher）、Git、Node.js，以及 Edge 或 Chrome。
2. 双击 install.cmd，等待依赖安装与双引擎检查完成。
   安装时需要联网访问 Python 包源和 GitHub，MediaCrawler 源码会自动下载。
3. 双击 start.cmd，浏览器会打开本机工作台。
4. 在网页顶部选择自己的视频和图片保存目录。
5. 打开平台登录窗口，用你自己的账号登录；验证码由你手动完成。

以后使用只需双击 start.cmd；停止前先完成录制保存，再双击 stop.cmd。
如果提示端口不可用，可参考 README.md，用 start.ps1 -Port 18967 更换端口。

默认本机访问地址：http://127.0.0.1:18966
这个地址只能在运行软件的那台电脑打开，把地址发给别人不能替代发送安装包。

本包不包含发送者的浏览器资料、Cookie、账号、任务记录、视频或截图。
软件会在接收者电脑上重新创建运行环境和保存目录。

许可与用途：
MediaCrawler Copyright (c) 2024 relakkes@gmail.com。
其代码使用 NON-COMMERCIAL LEARNING LICENSE 1.1（非商业学习使用许可证 1.1）。
本集成按个人非商业学习与研究用途提供；未经上游许可，不能用于商业用途。
完整许可证附在 third_party/MediaCrawler-LICENSE.txt，安装后的上游源码也保留该声明。
yt-dlp 及其他第三方依赖分别保留各自的许可证。

详细操作、功能限制和验证范围见 README.md。

照片与截图去重：支持 ImageHash 相似识别、保留选择、去重副本导出与可恢复隔离。
普通使用者推荐使用 VideoDesk-2.3.0-Windows-x64.exe，双击自动准备全部运行环境，并在就绪后隐藏到系统托盘。

1.3 支持 YouTube、视频链接不落盘直接抽帧、本机视频直接读取与可选音轨。
2.0 增加 dsh-video-storyboard 分镜与台词对齐、本地 ASR、OCR、字幕长图、细看片段和 AI ZIP。
语音模型可在线安装，或导入一起发送的 SenseVoice-offline.tar.bz2。
命令行与 DSH 插件说明见 README.md；实测范围见 MERGE-TEST-REPORT.md。
