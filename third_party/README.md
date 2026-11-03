# 第三方组件

本工具通过 Python 包复用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，通过独立进程接入 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)。没有将两个项目的依赖装入同一个环境。

- yt-dlp：Unlicense，具体以安装版本的包内许可证为准。
- MediaCrawler：Copyright (c) 2024 relakkes@gmail.com，NON-COMMERCIAL LEARNING LICENSE 1.1（非商业学习使用许可证 1.1）。本集成用于个人非商业学习与研究。上游源码的版权和许可证均保留，不以本工具的许可覆盖它们。
- MediaCrawler 固定源码提交：`8f583999006d5b692272c608440e193ca282e73b`。
- FFmpeg 来自 imageio-ffmpeg 的预编译发行包；运行 `python -m pip show imageio-ffmpeg` 查看包来源，实际构建信息由 `ffmpeg -version` 显示。

`setup.ps1` 将上游克隆到 `third_party/MediaCrawler`，其完整许可证位于 `third_party/MediaCrawler/LICENSE`。该目录保留独立 Git 元数据，并由主项目忽略；复制工具到其他电脑时运行安装脚本恢复它。

适配逻辑集中在 `integrations/mediacrawler_bridge.py`；不修改上游源码。运行时仅替换浏览器启动入口（使用本机 Edge / Chrome 与专用资料目录）及 FFmpeg 查找入口。

- 图片去重引擎：[ImageHash](https://github.com/JohannesBuchner/imagehash) 4.3.2，Copyright (c) 2013-2022 Johannes Buchner，BSD-2-Clause。通过官方 PyPI 包调用 pHash 与 dHash，完整版权和许可见 `ImageHash-LICENSE.txt`，包内 dist-info 也随 EXE 保留。

- YouTube 支持脚本：[yt-dlp/ejs](https://github.com/yt-dlp/ejs)，yt-dlp-ejs 0.8.0，随 yt-dlp[default] 安装，Unlicense / MIT / ISC 声明见 `yt-dlp-ejs-LICENSE.txt` 和包内 dist-info。运行时使用已内置的官方 Node.js；直连抽帧复用 [FFmpeg](https://github.com/FFmpeg/FFmpeg)，没有另行重写解码引擎。
