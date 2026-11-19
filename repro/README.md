# QA-27 复现包说明（映格 VideoDesk 2.7.0）

本目录是 2026-11-14 独立测试的**最小复现与证据集**。所有时间见 `BUG-REPORT.md`，均为 UTC+08:00。

## 目录结构

```
repro/
  README.md                 本文件
  environment.json          脱敏环境、实际构建标识、时间/时区、环境变更记录
  scripts/
    qa_client.py            从实例的 data/server.json 读取端口的 HTTP 客户端（不硬编码端口）
    make_sentinels.py       生成 7 个保护哨兵 + 记录 SHA256
    verify_sentinels.py     复核哨兵是否未被改动/删除
    make_media.py           用发布包内置 FFmpeg 生成小型合成媒体样例
    probe.py                第一轮 API 探测（49 条原始请求/响应）
    phone_bridge_probe.py   手机桥接 PC 侧逻辑（合成事件，不碰真机配对）
    source_repro.py         对 EXE 内同源模块的确定性函数级复现
    run_matrix.py           主黑盒回归；生成 TEST-MATRIX.csv 与 matrix_raw.json
    scan_secrets.py         脱敏（会话 token 打码）+ 敏感字段扫描
  samples/                  小型合成样例（可公开再分发，无真实素材）
  evidence/                 结构化响应、哈希、复现结果（JSON）
  logs/                     脱敏日志、源码测试原始输出
  qa/                       【测试沙盘，见下方「打包排除」】
```

## 顺序、依赖与前置

1. **确认构建**：`sha256sum release/VideoDesk-2.7.0-Windows-x64.exe release/VideoDesk-Android-1.0.0.apk`，应与 `environment.json` 一致。本轮 EXE/APK 与目标哈希完全一致，源码与 EXE 内 payload 11/11 同哈希（`evidence/payload_match.json`）。
2. **隔离**：使用 `--self-test --home <QA绝对路径> --report <报告绝对路径> --stop-file <停止标记绝对路径>`。**`--home` 不隔离默认素材目录**，必须在 `home/data/settings.json` 预置 `output` 指向 QA 目录，并在启动后用 `GET /api/state` 复核。报告中的端口一律来自该实例 `data/server.json`。
3. **依赖**：Python 3.13（发布包内置 `runtime/main/python.exe` 依赖最全）、Node 22、发布包内置 FFmpeg/ffprobe。仅运行脚本不需要额外 pip 安装；`probe.py`/`run_matrix.py` 用标准库 `urllib`。
4. **运行**：按 `BUG-REPORT.md` 第 8 节命令顺序执行。`source_repro.py` 必须用发布包内置 Python（普通 shell Python 缺 fastapi 等依赖）。
5. **停止**：创建 `--stop-file` 指定的标记文件，self-test 即结束；最长约 10 分钟自然超时退出属正常，**不算崩溃**。不要停止用户原有服务。

## 各 Bug 的最小复现

| Bug | 最短入口 | 关键证据 |
| --- | --- | --- |
| VD27-001 | `run_matrix.py` 的 A-dedup-* 请求；或 `source_repro.json` 的 `A7_link_normalization` | `evidence/matrix_raw.json`, `evidence/source_repro.json` |
| VD27-002 | `source_repro.py` → `A2_account_switch_without_owner_param` | `evidence/source_repro_run1.json`, `_run2.json` |
| VD27-003 | `source_repro.py` → `A3_partial_list_message` | 同上 |
| VD27-004 | `run_matrix.py` 的 B-001/B-002 两轮 | `evidence/output_divergence.json`, `evidence/matrix_raw.json` |
| VD27-005 | `source_repro.py` → `B2_missing_status_claimed_online` | `evidence/source_repro_run1.json`, `_run2.json` |
| VD27-006 | `run_matrix.py` 的 D-services-* 两轮 | `evidence/credential_clear.json` |

全部 FAIL 用例均**至少复现 2 次**（两轮不同输入，或同输入连续两次结果一致），次数写在 `TEST-MATRIX.csv` 的 `attempts` 列与 `BUG-REPORT.md` 第 1 节。

## 样例与哨兵

- `evidence/samples.json`：7 个合成样例的 SHA256、字节数、生成参数；`scripts/make_media.py` 可重建。样例为 FFmpeg lavfi 生成的纯合成内容，可自由再分发。
- `evidence/sentinels.json`：7 个顶层保护哨兵（原片 / 用户新增 / 共享 / 生成副本 / 零字节 / 损坏媒体 / 中文空格逗号长名）。`verify_sentinels.py` 在全部测试结束后复核为 `ok=true`。
- 清理测试另在 QA 任务目录内放置 3 个哨兵并修改 1 个生成文件，结果见 `evidence/f_cleanup.json`；清理只回收已登记且未修改的生成副本。

## 脱敏

- `scripts/scan_secrets.py` 对 `BUG-REPORT.md`、`TEST-MATRIX.csv`、`environment.json`、全部脚本、证据 JSON 与日志做正则扫描，并把 API 证据里捕获的会话 `token` 打码为 `<redacted>`；结果见 `evidence/secrets_scan.json`。
- 已删除隔离实例自动生成的桥接配对密钥文件，并把测试用假云服务密钥清空。用户名、手机序列号、私人目录、真实 Cookie/Authorization/session token/API 密钥均未写入交付物。
- **本轮未采集任何截图、平台页面、录像或音视频媒体**，因此无图片/媒体需人工二次检查；未保存完整登录页面，未保存个人关注列表。
- ⚠️ 自动扫描不能保证绝对无泄露，上述为已执行的检查与人工复核范围，不作绝对保证。

## 打包排除（重要）

`repro/qa/` 是**隔离测试沙盘**，不是证据包内容，包含发布包解包出的完整运行时（约 1.5 GB）、浏览器用户目录和应用数据库。交付/压缩时请排除：

```
repro/qa/home/versions/      发布包内置运行时（虚拟环境、模型、二进制）
repro/qa/home/data/browser-profile/   浏览器用户目录
repro/qa/home/data/jobs.json* / material-history.json  应用数据库
repro/qa/backup-cleanup/     清理测试的私有备份
```

`repro/qa/self-test-report*.json`、`repro/qa/home/launcher.log`、`repro/qa/home/data/settings.json` / `live-settings.json` 体量极小且已脱敏，保留作为「实例身份、端口、输出目录、降级恢复」的证据。

`E:/VideoDesk/.local/android-signing-key.jks`、`android-keypass.txt` 等签名材料属于用户项目文件，**未读取内容、未复制、未打包**。

## 边界声明

本轮未修改产品代码、未重建发布包、未发布、未替换用户桌面软件、未清理用户文件；未执行 APK 覆盖安装、`adb reverse`、通知读取、系统录屏或跳转抖音（会影响用户当前手机配对/需人工授权）。真实抖音登录态不可用，A/B/C 的真实平台链路见 `BUG-REPORT.md` 第 5、6 节的 BLOCKED 说明，未填 PASS。
