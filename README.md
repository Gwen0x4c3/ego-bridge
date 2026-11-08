# ego bridge

**Use your Mac's ego-lite browser from remote AI agents.**

让 Linux devbox 上的 Claude Code / Codex 使用 Mac 浏览器；窗口与登录态留在 Mac，你随时可以手动接管。

[![CI](https://github.com/gwen0x4c3/ego-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/gwen0x4c3/ego-bridge/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/gwen0x4c3/ego-bridge)](https://github.com/gwen0x4c3/ego-bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[For you](#for-you) · [For agents](#for-agents) · [Releases](https://github.com/gwen0x4c3/ego-bridge/releases) · [Security](docs/SECURITY.md)

<p>
  <img src="docs/images/agent-active.png" width="360" alt="Light macOS panel with Agent-space activity">
  <img src="docs/images/connected-dark.png" width="360" alt="Dark macOS connection panel">
</p>

原生 macOS 面板的演示渲染，使用示例设备名与任务数据。

<img src="docs/images/tray-icons.png" width="360" alt="Original browser and cursor icon: connected, connecting, error, paused">

## What it does

- Mac 主动出站连接 Linux，TLS + 一次性配对 + 证书指纹校验，Mac 不监听网络端口。
- 菜单栏 App 与 Mac `ego-bridge` CLI 共用一个核心，开关、配对和配置实时同步。
- Linux 使用同名 `ego-browser` shim，透传官方 CLI 的参数、stdin、输出与退出码。
- Session 目录内的截图/文件自动回传；超时或断链不自动重放浏览器操作。
- Popup 显示 Agent 空间、控制权、最近页面及当前桥接命令数。

```text
Linux devbox                                    Mac
AI agent → ego-browser shim → TLS service ←──── EgoBridge.app
                                :3325    outbound     │
                                                      ├─ ego-bridge CLI (shared core)
                                                      └─ official ego-browser → ego-lite
```

## For you

### 1. Mac：一条命令安装 App + CLI

先安装官方 **ego-lite** 并完成首次引导。桥接 App 支持 macOS 13+；本项目不分发浏览器本体。

推荐 **Homebrew**，会同时安装 App、`ego-bridge` CLI 和 Python，**不需要自己 build**：

```bash
brew install --cask gwen0x4c3/tap/ego-bridge
open -a EgoBridge
```

没有 Homebrew，但已有可用的 Python 3.9+ 时，可以自动下载、校验并安装预编译 Release：

```bash
curl -fsSL https://github.com/gwen0x4c3/ego-bridge/releases/latest/download/install.sh | bash
```

脚本自动选择 Apple Silicon / Intel，安装到 `~/Applications/EgoBridge.app` 和
`~/.local/bin/ego-bridge`，然后打开 App。不需要 Git、Node.js、Swift 或 Xcode。
也可直接在 [Releases](https://github.com/gwen0x4c3/ego-bridge/releases/latest) 下载 App ZIP。

升级或从源码版迁移前，先退出正在运行的旧 App。若 `command -v ego-bridge` 仍指向旧
源码目录，移除那个旧链接或调整 PATH，避免它遮住 Homebrew 的命令。配对资料保存在 App
外，不会随安装包替换而清除。当前 App 为临时签名、尚未公证，首次打开可能需要你在
macOS 中确认；安装器不会关闭 Gatekeeper。

### 2. Linux：一条命令安装 CLI + 服务

有 Python 3.7+、OpenSSL、Bash 和 curl 的 Linux，可以直接安装 Release 并启动 systemd
用户服务；**不需要源码仓库或本地浏览器**：

```bash
curl -fsSL https://github.com/gwen0x4c3/ego-bridge/releases/latest/download/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
ego-browser pair
```

没有 systemd 用户会话时，脚本会给出 `ego-agent-service` 手动启动命令。安装器保留已有
配对，只替换本项目的命令，拒绝覆盖无关的 `ego-browser`。

如果 Linux 已经使用 Homebrew，也可以让它处理 Python/OpenSSL 依赖和服务：

```bash
brew install gwen0x4c3/tap/ego-bridge-agent
brew services start gwen0x4c3/tap/ego-bridge-agent
ego-browser pair
```

选择一种服务管理方式即可，不要同时用 Homebrew 和独立安装器启动两份服务。
Mac 必须能访问这台 Linux 的 TCP 3325 端口。

### 安装版本与更新

两端共用同一个安装脚本，自动识别平台。固定版本、指定目录或只安装而不启动：

```bash
curl -fsSL https://github.com/gwen0x4c3/ego-bridge/releases/download/v2.3.0/install.sh \
  | bash -s -- --version v2.3.0 --no-start
# 高级选项：--prefix /absolute/path（CLI/runtime），EGO_BRIDGE_APP_DIR（Mac App 目录）
```

Homebrew 更新使用 `brew update` / `brew upgrade`；独立安装器重新运行上述 latest 命令。
Homebrew 定义在 [gwen0x4c3/homebrew-tap](https://github.com/gwen0x4c3/homebrew-tap)，每六小时
自动同步新 Release，也可手动触发 Sync 工作流。

### 3. Pair once

将 Linux 输出的五分钟一次性配对码填入 Mac 面板，连同 devbox 主机名和端口一起保存。
也可以让 Mac 上的 Agent 执行以下命令，并通过 stdin 输入配对码：

```bash
ego-bridge connect --host YOUR_DEVBOX --port 3325 --code-stdin
```

确认两端状态：

```bash
# Mac
ego-bridge --json status
# Linux
ego-browser bridge status
ego-browser --version
```

之后打开 App 即按保存的开关状态自动连接。日常控制：

```bash
ego-bridge disconnect                        # 暂停，保留配对
ego-bridge connect                           # 恢复并等待就绪
ego-bridge configure --host YOUR_DEVBOX       # 更新地址，保留证书校验
ego-bridge unpair                            # 删除配对
```

## For agents

安装符合 [Agent Skills 规范](https://agentskills.io/specification) 的桥接技能：

```bash
npx skills add gwen0x4c3/ego-bridge --skill ego-bridge
```

固定当前发布版本：

```bash
npx skills add gwen0x4c3/ego-bridge@v2.3.0 --skill ego-bridge
```

[skills CLI](https://github.com/vercel-labs/skills) 默认安装到当前项目；加 `-g` 全局安装，
用 `-a codex` 或 `-a claude-code` 指定 Agent。当前 CLI 需要 Node.js 22.20+。

**安装 skill 只安装说明，不会自动安装 App、Linux 服务或命令行运行时。**
技能内置了 [完整安装指引](skills/ego-bridge/references/install.md)，包括获取源码、判断
Mac/Linux 角色、安装运行时、配对和验证，避免只装 skill 就把配置任务标为完成。
可以直接将下面的话交给 Agent：

> 使用 $ego-bridge 配置完整的桥接环境。先判断当前机器角色，安装对应的运行时，再完成配对和连接验证。另一端无法访问时，给我明确的待完成步骤。

Linux 上开始浏览器任务前，先运行 `ego-browser skill` 并读到结束标记，获取与当前浏览器
版本一致的 API 指南。最小示例：

```bash
ego-browser nodejs <<'JS'
const task = await taskSpace("inspect example page");
const page = task.page("p1");
await page.goto("https://example.com");
console.log({spaceId: task.spaceId, title: await page.title()});
await task.finish({keep: []});
JS
```

用户接管时停止操作，未经同意不要抢回控制权。截图保存到
`/tmp/ego-bridge/<session>/` 后自动回传；断链或超时后，用
`ego-browser bridge result REQUEST_ID` 查询结果，不要直接重放操作。

## Scope and limits

- 当前是受信任机器之间的远程执行通道，不是浏览器沙箱；见 [安全模型](docs/SECURITY.md)。
- 不支持经桥接执行交互 REPL、`import`、`upgrade`、位置参数脚本文件或上传文件。
- 活动区显示空间/最近页面与桥接命令，不是逐点击的全局动作流；见 [Agent activity](docs/AGENT_ACTIVITY.md)。
- Extra headers 已验证同轮导航、刷新和 fetch；新页面及下一轮不继承，见 [验证与用法](docs/EXTRA_HEADERS.md)。
- 官方 ego-lite 是独立依赖；已在 CLI 0.6.2.9 上完成真机验证，不承诺所有未来版本兼容。

## Development and releases

- [测试与本地开发](docs/TESTPLAN.md)
- [贡献与发布流程](CONTRIBUTING.md)
- [更新记录](CHANGELOG.md)
- [GitHub Releases](https://github.com/gwen0x4c3/ego-bridge/releases)

源码构建只面向开发：`bash mac/install.sh`。普通安装请使用上面的 Homebrew / Release 入口。

`VERSION` 是发布版本号。推送匹配的 `vX.Y.Z` tag 后，Release workflow 运行测试、构建
Apple Silicon / Intel App 和 Linux 安装包，并发布 SHA-256 校验和。

代码与原创图标以 [MIT](LICENSE) 授权；不包含官方 ego-lite 的代码或二进制文件。
