# NJU Connect

基于 [zju-connect](https://github.com/Mythologyli/zju-connect) 的南京大学 aTrust VPN 命令行工具（Linux）：

- 一条命令安装 `zju-connect` 和 `nju-connect`，账号密码单独保存在权限为 600 的配置文件中
- 后台服务自动检测是否在校园网：校外自动连接 VPN，回到校内自动断开
- 根据学校下发给你账号的访问策略，自动生成 Clash/mihomo 规则集和 Clash Verge Rev 全局扩展脚本，并每 30 分钟更新

## 安装

需要 Linux、`curl` 和 Python 3.8+。

```bash
curl -fsSL https://raw.githubusercontent.com/Huaji-tye2007/zju-connect/main/nju-connect/install.sh | bash
```

安装程序会：

1. 从 [Releases](https://github.com/Huaji-tye2007/zju-connect/releases) 下载对应架构的 `zju-connect`（没有预编译文件且装有 Go 时从源码编译）
2. 把 `zju-connect` 和 `nju-connect` 安装到 `~/.local/bin`
3. 运行 `nju-connect setup`，询问学号、密码、登录方式以及是否修改默认代理端口（SOCKS5 1080 / HTTP 1081，直接回车保持默认）
4. 询问是否启用后台服务

重新运行安装命令即可升级，已有配置不会被覆盖。可用 `NJU_CONNECT_VERSION=<tag>` 安装指定版本，`NJU_CONNECT_PREFIX=<目录>` 修改安装位置。

## 首次使用

```bash
nju-connect connect           # 首次登录（可能需要输入短信验证码），连接成功后 Ctrl+C 退出
nju-connect ruleset           # 生成 Clash 规则集
nju-connect clash-script --install   # 安装 Clash Verge Rev 全局扩展脚本
nju-connect service install   # 启用后台服务
```

登录状态保存在 `client_data.json` 中，之后的登录（包括后台服务）会复用它，不需要再输入验证码。

## 命令

| 命令 | 作用 |
|---|---|
| `nju-connect setup` | 创建或修改配置（账号、密码、登录方式、代理端口） |
| `nju-connect connect` | 前台连接，用于首次登录或输入短信验证码 |
| `nju-connect trust` / `untrust` | 把本机设为授信终端 / 取消授信（授信后登录免短信） |
| `nju-connect ruleset` | 下载访问策略并生成规则集；`--from-file` 使用上次下载的策略 |
| `nju-connect clash-script` | 输出 Clash Verge Rev 全局扩展脚本；`--install` 直接写入（自动备份旧脚本），`--format yaml` 输出 mihomo 配置片段，`--inline` 把规则直接写进脚本 |
| `nju-connect service install\|uninstall\|start\|stop\|restart\|status\|logs` | 管理 systemd 用户服务 |
| `nju-connect check` | 查看网络位置、VPN、服务和规则集状态 |
| `nju-connect uninstall [--purge]` | 删除服务和程序；`--purge` 同时删除配置和登录状态 |

如果已有 zju-connect 在运行，或代理端口被占用，`connect` 和 `service install` 会显示进程号并提示先停止它（`--force` 可跳过检查）。

## 后台服务如何工作

`nju-connect service install` 会创建 `~/.config/systemd/user/nju-connect.service`，每分钟检查一次：

- **是否在校园网**：直接向南大内网 DNS（10.12.253.4、10.28.253.4）查询。只有在校园网内才会得到应答。
- **校外**：启动 zju-connect；通过 SOCKS5 代理向内网 DNS 查询来检查 VPN 是否可用，连续 3 次失败则重启；VPN 可用时每 30 分钟更新规则集。
- **校内**：停止 zju-connect。
- **登录失败**（例如登录状态过期且需要短信验证码）：逐渐延长重试间隔（最长 30 分钟），并弹出桌面通知。此时运行 `nju-connect service stop && nju-connect connect` 完成登录，再 `nju-connect service start`。
- 如果端口上已有其他 zju-connect 在运行，服务不会再启动一个，只负责更新规则集。

查看日志：`nju-connect service logs`。

## Clash 集成

生成的脚本会注入：

- 代理 `NJUConnect`：指向配置文件中的 SOCKS5 端口（支持 UDP）
- 策略组 `NJU`：`fallback` 类型，包含 `[NJUConnect, DIRECT]`，每 5 分钟通过 NJUConnect 访问 `http://lib.nju.edu.cn/` 检测。VPN 在线时走 NJUConnect，zju-connect 停止时（例如在校内）自动改为直连
- 规则集 `nju-vpn`：只包含学校允许通过 VPN 访问的地址，按域名、端口和协议精确匹配
- 规则：VPN 服务器本身直连（避免开启 TUN 模式时形成回环），然后是 `RULE-SET,nju-vpn,NJU`

规则集位置：

- Clash Verge Rev：`~/.local/share/io.github.clash-verge-rev.clash-verge-rev/ruleset/nju-vpn.yaml`，脚本写入其 `profiles/Script.js`（全局扩展脚本）
- mihomo：`~/.config/mihomo/ruleset/nju-vpn.yaml`
- 其他客户端：规则会直接写进脚本（`--inline`），因为 mihomo 只允许读取其主目录下的规则文件；规则更新后需重新生成脚本

规则集更新后，在 Clash Verge 中重新加载订阅即可生效。

## 文件位置

| 路径 | 内容 |
|---|---|
| `~/.local/bin/zju-connect`、`~/.local/bin/nju-connect` | 程序 |
| `~/.config/nju-connect/config.toml` | zju-connect 配置，含密码（权限 600） |
| `~/.config/nju-connect/nju-connect.conf` | nju-connect 设置：校园网检测、更新间隔、规则集路径、Clash 策略组 |
| `~/.local/state/nju-connect/client_data.json` | 登录状态 |
| `~/.local/state/nju-connect/resource.json` | 最近一次下载的访问策略 |

## 常见问题

- **每次都要短信验证码**：运行 `nju-connect trust` 把本机设为授信终端。学校限制每个账号最多 3 台电脑、3 台手机；超过时会失败（错误码 75500311），需要先在其他设备上取消授信。
- **开启 Clash TUN 模式后服务误判为在校内**：TUN 会把内网 DNS 查询也转发进 VPN。请在 TUN 设置中把 10.12.253.4、10.28.253.4 排除，或关闭 TUN 使用系统代理。
- **不使用 systemd**：在桌面自启动中运行 `nju-connect daemon` 即可。
