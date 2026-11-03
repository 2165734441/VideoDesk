# 映格 DSH 插件

先运行映格 2.4 EXE，再用 DSH 自带的插件安装流程安装本目录或发布包。插件通过本机 API 调用同一套任务，不另开采集后台。

注册 `video_desk_storyboard`、`video_desk_zoom`、`video_desk_probe`、`video_desk_locate`、`video_desk_subtitles`。工具使用映格的浏览器账号、模型与保存目录。不会上传 Cookie 或复制账号。

默认从 `%LOCALAPPDATA%\VideoDesk\data\server.json` 查找服务。自定义安装目录用 `VIDEO_DESK_HOME`，开发服务用 `VIDEO_DESK_URL=http://127.0.0.1:端口`。DSH 附件服务可用时直接回传图片，否则返回本机目录和文本。单次最多展示 24 张图，完整结果仍在素材目录。

例如：`video_desk_storyboard({input:"D:/视频/讲解.mp4",transcript:"auto"})`；随后 `video_desk_zoom({job_id:"返回的任务ID",cell:3})`。

本适配层 Apache-2.0。分镜算法采用 SAOS-3624/dsh-video-storyboard；映格中的 MediaCrawler 仍遵守其非商业学习许可证，完整许可见应用第三方目录。


2.1 新增 `video_desk_batch`、`video_desk_export`，原有五个工具保留。

```js
video_desk_batch({inputs:["D:/视频/商品 A.mp4","D:/视频/商品 B.mp4"],outputs:["frames","audio"],kind:"rules",segment_seconds:10,dedup:{enabled:true,mode:"similar",threshold:4}})
video_desk_batch({folder:"D:/视频",recursive:true,outputs:["puzzles","transcript","ai"]})
video_desk_export({job_id:"任务ID",outputs:{audio:true},request_id:"稳定的唯一请求标识"})
```

`inputs` 为路径数组，`path_lines` 为多行路径；首尾引号由共用后端去除。传 `request_id` 可让断线重试复用任务。网页文件来源需在映格网页重新挂接，插件不保存视频内容。未安装真实 DSH 宿主时的验证范围为适配器、实际 API 与图片附件协议，不能代替具体宿主版本的安装验证。
