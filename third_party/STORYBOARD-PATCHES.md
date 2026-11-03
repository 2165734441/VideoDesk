# dsh-video-storyboard 集成记录

上游：https://github.com/SAOS-3624/dsh-video-storyboard

固定提交：e124ae6fcfb31f3572ea24aad093a86897b556a3，版本 0.6.0，Apache-2.0。原始 LICENSE、NOTICE、CREDITS.md 随源码保留。

初始集成修改三处（后续补丁见下文）：

1. `src/storyboard/fetch.js`：当输入严格等于控制器传入的 `VIDEO_DESK_DIRECT_INPUT` 且为 127.0.0.1 动态端口时，直接读取内存转发流，不执行上游 HTTP 下载。
2. `src/storyboard/transcript-pass.js`：读取由控制器生成的字幕文件时，按 `VIDEO_DESK_TRANSCRIPT_SOURCE=asr` 保留语音来源，允许上游 OCR 抽检校正。其余现成字幕继续标为文件来源。
3. `src/storyboard/pipeline.js`：允许控制器传入单独的 `audioInput`。YouTube 等平台的直接模式可能将视频与声音分为两条流，音频测量和拼图底部音量条读取对应音轨，仍不保存视频。

映格适配层位于 `integrations/storyboard*`、`media_relay.py`、`ai_models.py`、`asr_worker.py`，负责统一任务、账号、来源、时间窗口、离线模型与导出。Node 桥统一隐藏子进程窗口，对外输出移除私有媒体地址；不将 DeepSeek 的 384 token/图估算推广到其他 AI。

网页、CLI 和 dsh-video-desk 插件调用同一个本机 API；原上游 CLI 源码继续保留供追溯，分享版推荐使用映格统一入口。

2.2 补丁：`board.js` 透传 `maxCellsPerSheet`；`pack.js` 新增保序等格分页排布；`pipeline.js` 在选帧完成后应用每图画面上限。超出会新建拼图，不因该上限删除已选画面。上游自动排布在参数为 0 时保持原行为。集中上传、全画面 OCR、联合时间轴及历史素材管理均位于映格适配层，不修改上游许可证。

2.2.1 补丁：`src/ocr_bridge.py` 对 RapidOCR 3.x 显式使用 CPU 并限制推理线程，保留旧版引擎接口；映格 Node 桥降低分镜取帧并发并限制 FFmpeg 线程。画面选取、拼图、台词对齐算法不因本次稳定性修改而删减内容。
