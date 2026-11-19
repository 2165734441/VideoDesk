# 映格 2.7.1 修复说明

本次依据项目根目录的 `BUG-REPORT.md` 和 `TEST-MATRIX.csv` 处理问题。原报告、70 条测试矩阵和 `repro/` 原始证据保持原样；本文件记录修复后的结果，不将原 FAIL 改成未经复测的 PASS。

## 问题处理

| 报告编号 | 处理结果 |
| --- | --- |
| VD27-001 | reflow 分享链接的协议、跟踪参数、片段和尾斜杠统一处理，同类变体拒绝重复添加。成功解析后的实际房间 ID、短链接落地页关系持久化，后续可识别已知跨链接重复。 |
| VD27-002 | 关注快照绑定登录会话指纹和账号资料；会话或账号变化立即清空旧快照，旧会话迟到响应被忽略。缺少全部身份依据时不合并旧列表，并提示重新登录、从首页读取。 |
| VD27-003 | API 增加 `missing_count`、`unread_count`，界面文案显示数值差额；总数未知返回空值，已读取超过总数时显示冲突，不出现负差额、不宣称完整。 |
| VD27-004 | 空直播输出目录持久化为“跟随顶部设置”，每次建任务按当时全局目录取值。界面增加跟随开关与实际路径提示；独立指定的目录和已开始的任务保留各自目录。 |
| VD27-005 | 只有明确 `status=2` 才认定开播，`status=4` 为未开播，其余为未知。录制管理器遇到未知状态报“无法确认”，不启动采集，也不显示未开播。 |
| VD27-006 | 服务配置支持显式空密钥和 `clear_api_key=true` 撤销；省略密钥保留旧值，未提交的服务不被重置。界面增加“删除已保存的密钥”，普通留空保存不会误删，删除后状态立即刷新。 |

### VD27-001 的验收口径修正

`https://live.douyin.com/<编号>` 路径携带公开 webRID，`https://webcast.amemv.com/douyin/webcast/reflow/<编号>/` 携带实际 room ID。两种编号不能只因字符串相同就认定为同一房间，更不能把 reflow 地址直接改写为 live 地址。原报告的合成输入不足以证明这两种地址必然对应同一主播。

修复采用分别归一化、解析成功后记录可信映射的方式：同一链接类型的变体去重；已知属于同一真实房间的短链／落地页／reflow 地址去重；没有映射证据的不同类型地址保留区分，实际录制仍按已解析的房间 ID 防重。重启后保留已学到的映射，编辑链接后丢弃旧映射，迟到结果不能写回已改过的链接。

旧主播记录不自动删除，避免丢失用户备注、时间计划和设备策略。旧版持久化目录与当前全局目录一致时恢复跟随；不同目录保守保留，用户可勾选“录播保存位置跟随顶部设置”后保存。

## 验证

- 新增 15 项针对性回归，覆盖账号／会话切换、owner 缺失、分页续读、迟到响应、差额、链接标识边界、可信映射持久化、目录跟随与重启、三态开播判断、密钥删除和保留。
- 最终源码 Python 回归：190 项通过，0 失败、0 错误、0 跳过。前端及手机检测 Node 测试：21 项通过，0 失败、0 跳过。
- 发布包、界面及桌面更新的最终验证以 `release/VideoDesk-2.7.1-acceptance.json` 为准，不能把源码测试通过代替发布包验证。

复现及验证代码：`tests/test_bug_report27.py`。本次使用已准备好的完整依赖、产品的 Windows 平台兼容初始化与实际 FFmpeg，未修改系统服务、驱动或全局代理。

Windows Python 3.13 的独立测试进程应与产品入口一样先做平台兼容初始化，再导入测试模块；在依赖完整的 Python 环境、源码根目录可运行：

```powershell
python -c "import os,sys; sys.path.insert(0,'.'); from integrations.runtime_support import configure_platform; configure_platform(); import imageio_ffmpeg,unittest; os.environ['IMAGEIO_FFMPEG_EXE']=imageio_ffmpeg.get_ffmpeg_exe(); r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests')); sys.exit(not r.wasSuccessful())"
node --test tests/test_dedup_ui.mjs tests/test_progress_ui.mjs tests/test_sheet_limit.mjs tests/test_phone_detector.mjs
```

## 未宣称已验证的部分

本次不把原报告中因登录、平台访问、Android 本人录屏授权或缺少独立测试设备而阻碍的真实关注导入、多路真实直播、手机真机录屏、完整翻译成片及速度比较改为通过。手机 APK 本次未修改，不需要重新授权安装。

报告中的代码审查候选继续单列：FFmpeg 录制凭据通过进程参数传递、Windows 回收站异常时的恢复保证、桥接连接状态的语义。它们没有因本次修复被标记为“已复现且已解决”；其中未知开播状态被当作未开播的候选已随 VD27-005 一并处理。

## 发布包与桌面验证结果

- 发布包 VideoDesk-2.7.1-Windows-x64.exe，SHA256：`39b8ca6e4b19e32cd6947a3cb71461cb5a98577d3f4bb313e9dd4e4eafa8b21a`。
- 9 个内置运行组件就绪，145 个应用文件与源码逐一匹配。
- 已通过独立发布包的目录跟随／独立目录、链接去重、API 密钥保留／删除、界面操作与无 JavaScript 错误验证。
- 桌面已更新至 2.7.1，稳定入口 EXE 哈希一致；原 8 个任务保留，9 个受保护配置／凭据文件哈希不变。
- 手机 APK 内容保持不变；本次没有发起录屏授权或手机录制。
- 本次独立测试环境和中间压缩包已移至 Windows 回收站，可恢复；原测试报告、复现证据和桌面回滚备份保留。
