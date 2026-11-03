# OpenCode 语音控制（离线 ASR）

按住 **F8** 或**鼠标侧键**说话 → 松开 → SenseVoice 本地转写 → 文本自动作为用户消息投递到当前 OpenCode 会话。

- ASR：sherpa-onnx + SenseVoice-small (int8)，纯 CPU，中文/中英混说，本机实测 11s 音频转写 0.28s
- 交互：全局热键 / 鼠标侧键按住说话（push-to-talk），无需唤醒词
- 投递：OpenCode HTTP API（Basic auth，自动从 `opencode service status` 发现端口）
- 打断：说“停止 / 取消”等词直接中断当前 agent 回合，不投递文本
- 纠错：`config.toml` 里的 `[terms]` 把常见误识别替换成代码写法（如“use state”→`useState`）
- MCP：`voice` 服务器提供 `status` / `listen` / `interrupt`，`listen` 让 agent 等待用户口头确认（该次语音不会自动提交）

## 目录

```
src/voice_asr/
  asr.py            SenseVoice 封装
  audio.py          麦克风采集、静音裁剪、重采样
  corrections.py    术语纠错
  opencode_client.py OpenCode 会话发现与 prompt 投递
  daemon.py         热键/鼠标守护进程 + 本地控制接口 (127.0.0.1:8756)
  mcp_server.py     MCP stdio 服务器（工具层）
  check.py          自检：设备列表 / 模型 / 转写
config.toml         配置
models/sensevoice/  SenseVoice int8 模型 (239MB) + tokens
logs/               运行日志
scripts/            MCP/投递自检、开机自启安装脚本
```

## 使用

```powershell
# 自检：列出输入设备，用麦克风录 4 秒并转写
uv run voice-check --record 4

# 转写一个 wav 文件
uv run voice-check --wav whisper.cpp\samples\jfk.wav

# 前台启动守护进程（调试用，能看实时日志）
uv run voice-daemon

# 静默启动（日常使用）
start-voice-silent.cmd
```

启动后按住 F8 或鼠标侧键说话，松开即发送（开始/结束各有一声提示音）。

```powershell
# 查看状态
curl.exe http://127.0.0.1:8756/status

# 无麦克风时直接测试投递
curl.exe -X POST http://127.0.0.1:8756/submit -H "Content-Type: application/json" -d "{\"text\":\"你好\"}"
```

## 配置要点（config.toml）

| 项 | 说明 |
| --- | --- |
| `hotkey.key` | 键盘触发键，`f1`..`f12` / `caps_lock` / `scroll_lock` / 单字符 |
| `hotkey.mouse` | 鼠标触发键，`x1`(后) / `x2`(前) / `middle`，`[]` 关闭 |
| `audio.device` | 留空用系统默认；填序号或名称片段（如 `HECATE`） |
| `commands.stop` | 说完即打断的短语（整句匹配，忽略标点） |
| `terms` | 误识别 → 目标写法；英文规则按单词边界匹配，不会破坏 `useState` |
| `opencode.directory` | 自动投递到该目录下最近更新的会话 |
| `opencode.session_id` | 固定投递到某个会话（优先级更高） |
| `opencode.allow_fallback` | 该目录无会话时是否退回全局最近会话 |
| `asr.language` | `auto` / `zh` / `en` / `ja` / `ko` / `yue` |

修改配置后重启守护进程。注意鼠标侧键同时也会被浏览器/资源管理器当作“前进/后退”，
如果觉得干扰，可以把 `hotkey.mouse` 改成 `["middle"]` 或 `[]`。

## MCP 注册

全局配置 `~/.config/opencode/opencode.json` 已注册：

```json
{
  "mcp": {
    "servers": {
      "voice": {
        "type": "local",
        "command": ["E:\\voice\\.venv\\Scripts\\python.exe", "-m", "voice_asr.mcp_server"],
        "cwd": "E:\\voice",
        "environment": { "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8" }
      }
    }
  }
}
```

MCP 服务器在需要时会自动拉起守护进程（`VOICE_NO_AUTOSTART=1` 可关闭）。

## 开机自启

已通过 `scripts/install-autostart.ps1` 在启动文件夹创建 `OpenCode Voice.lnk`。
重新安装/更换路径时重跑：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
```
