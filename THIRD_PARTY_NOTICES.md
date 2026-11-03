# 第三方来源、致谢与许可证

映格 / VideoDesk 的原创代码采用 [Apache-2.0](LICENSE)。第三方代码、模型和外部工具保留各自版权与许可证；根目录许可证不覆盖第三方的限制。

## 作者与项目来源

部分功能建立在 B 站 UP 主 **[苍穹晓雷（UID：109659565）](https://space.bilibili.com/109659565)** 的项目基础上，感谢其公开分享。

VideoDesk 的整合与后续开发：**[沙雕狗_（UID：454711645）](https://space.bilibili.com/454711645)**。

分镜相关源码来自 [SAOS-3624/dsh-video-storyboard](https://github.com/SAOS-3624/dsh-video-storyboard)，版本 0.6.0，固定提交 `e124ae6fcfb31f3572ea24aad093a86897b556a3`，采用 Apache-2.0。本仓库保留其 [LICENSE](third_party/dsh-video-storyboard/LICENSE)、[NOTICE](third_party/dsh-video-storyboard/NOTICE) 和 [CREDITS.md](third_party/dsh-video-storyboard/CREDITS.md)。本项目的适配修改列于 [STORYBOARD-PATCHES.md](third_party/STORYBOARD-PATCHES.md)。上游文档中的“不分发第三方组件”等表述指上游项目，本项目的分发范围以本文件为准。

本仓库不分发上游文档中的真实视频示例图片、赞赏二维码及私人测试视频。原始说明可在上游仓库查看。致谢不表示上游作者为本项目背书。

## 依赖与分发范围

| 组件 | 用途 | 来源与许可证 |
| --- | --- | --- |
| dsh-video-storyboard | 镜头检测、选帧、分镜拼图和时间索引 | Apache-2.0；含本地适配的源码与原始声明 |
| yt-dlp / yt-dlp-ejs | 视频链接解析、下载与站点支持 | [yt-dlp](https://github.com/yt-dlp/yt-dlp)、[ejs](https://github.com/yt-dlp/ejs)；按安装包保留的 Unlicense / MIT / ISC 等声明使用；见 [ejs 许可](third_party/yt-dlp-ejs-LICENSE.txt) |
| MediaCrawler | 可选的平台内容采集 | [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，固定提交 `8f583999006d5b692272c608440e193ca282e73b`；**非商业学习使用许可证 1.1**，Copyright (c) 2024 relakkes@gmail.com；见 [完整许可证](third_party/MediaCrawler-LICENSE.txt) |
| FFmpeg / ffprobe | 视频检查、解码、裁剪、音轨和视频片段导出 | [FFmpeg](https://ffmpeg.org/)、[Gyan 构建](https://github.com/GyanD/codexffmpeg)及 [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)；以实际构建的 GPL / LGPL 及附带声明为准，见 [工具许可](runtime-tools/LICENSE)与 [构建来源](runtime-tools/media-tools.json) |
| ImageHash | 图片重复与相似度比较 | [JohannesBuchner/imagehash](https://github.com/JohannesBuchner/imagehash)，BSD-2-Clause；[完整许可证](third_party/ImageHash-LICENSE.txt) |
| RapidOCR / ONNX Runtime | 本地画面文字识别与模型推理 | [RapidOCR](https://github.com/RapidAI/RapidOCR)、[ONNX Runtime](https://github.com/microsoft/onnxruntime)；使用所安装发行包及模型附带的许可证 |
| sherpa-onnx / SenseVoice | 本地语音转写 | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)、[SenseVoice](https://github.com/FunAudioLLM/SenseVoice)；模型开发者为 Alibaba FunAudioLLM 团队；保留 SenseVoice 名称，见 [模型目录](integrations/model-catalog.json)与 [所选项目许可](third_party/SenseVoice-LICENSE.txt)，模型权重另行获取并遵循其随附协议 |
| Python / Node.js / Playwright 及其他 Python 包 | 本地服务、界面、浏览器和命令行运行环境 | 源码仓库通过依赖文件引用；安装版本及其许可证以 [锁定文件](requirements-lock.txt) 和发行包中的声明为准 |

**MediaCrawler 不是无用途限制的开源许可。** 它只授权非商业学习和研究，禁止大规模采集及干扰平台运营；未经作者书面同意不得商用。VideoDesk 的 Apache-2.0 许可不改变这些条件。本仓库只保留其许可证和适配代码，安装脚本按固定提交获取上游源码。

公开源码包不包含 FFmpeg/ffprobe 可执行文件、Python/Node 运行时、浏览器、OCR/ASR 模型权重或 MediaCrawler 源码。安装脚本从对应发布者获取所需组件；重新分发包含这些组件的成品时，应同时满足这些组件的分发要求。
