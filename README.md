# NJU Connect CLI

基于 [zju-connect](https://github.com/Mythologyli/zju-connect) 的南京大学 aTrust VPN 命令行工具（Linux）：

- 一条命令安装 `zju-connect` 和 `nju-connect`，账号密码单独保存在权限为 600 的配置文件中
- 后台服务自动检测是否在校园网：校外自动连接 VPN，回到校内自动断开
- 根据学校下发给你账号的访问策略，自动生成 Clash/mihomo 规则集和 Clash Verge Rev 全局扩展脚本，并每 30 分钟更新

## 安装

需要 Linux、`curl` 和 Python 3.8+。

```bash
curl -fsSL https://raw.githubusercontent.com/Huaji-tye2007/nju-connect-cli/main/install.sh | bash
```

安装程序会：

1. 从 [Huaji-tye2007/zju-connect](https://github.com/Huaji-tye2007/zju-connect/releases) 下载对应架构的 `zju-connect`（没有预编译文件且装有 Go 时从源码编译），从本仓库 [Releases](https://github.com/Huaji-tye2007/nju-connect-cli/releases) 下载 `nju-connect`
2. 把 `zju-connect` 和 `nju-connect` 安装到 `~/.local/bin`
3. 运行 `nju-connect setup` 完成首次配置（见下文）

运行 `nju-connect upgrade`（或重新运行安装命令）即可升级，已有配置不会被覆盖，后台服务会自动重启。可用 `NJU_CONNECT_VERSION=<tag>` / `ZJU_CONNECT_VERSION=<tag>` 安装指定版本，`NJU_CONNECT_PREFIX=<目录>` 修改安装位置。

## 首次使用

安装程序会自动运行 `nju-connect setup`（也可以随时手动运行），依次完成：

1. 询问学号、密码、登录方式，以及是否修改默认代理端口（SOCKS5 1080 / HTTP 1081，直接回车保持默认）
2. **首次登录**：在当前终端运行 zju-connect，需要时输入短信验证码；登录成功、保存登录状态后自动退出
3. 下载访问策略，生成 Clash 规则集
4. 检测到 Clash Verge Rev 时询问是否安装全局扩展脚本
5. 询问是否启用后台服务（校外自动连接）

最后只列出被跳过、仍需手动完成的步骤。登录状态保存在 `client_data.json` 中，之后的连接（包括后台服务）都会复用它，不需要再输入验证码；登录状态过期时运行 `nju-connect login` 重新登录即可。

## 命令

| 命令 | 作用 |
|---|---|
| `nju-connect setup [--advanced]` | 创建或修改配置（账号、密码、登录方式、代理端口）；`--advanced` 还会询问后台服务、Clash 和校园网检测的设置 |
| `nju-connect config show\|get\|set` | 查看或修改单项设置，见下文「配置」 |
| `nju-connect login` | 在终端中登录（需要时输入短信验证码）并保存登录状态；后台服务运行时会自动暂停再恢复 |
| `nju-connect connect` | 在后台连接，连上后立即返回（已启用后台服务时改为启动服务）；`-f` 在前台运行并显示 zju-connect 输出 |
| `nju-connect disconnect` | 断开后台连接（或停止后台服务） |
| `nju-connect trust` / `untrust` | 把本机设为授信终端 / 取消授信（授信后登录免短信） |
| `nju-connect ruleset` | 下载访问策略并生成规则集；`--from-file` 使用上次下载的策略 |
| `nju-connect clash-script` | 输出 Clash Verge Rev 全局扩展脚本；`--install` 直接写入（自动备份旧脚本），`--format yaml` 输出 mihomo 配置片段，`--inline` 把规则直接写进脚本 |
| `nju-connect service install\|uninstall\|start\|stop\|restart\|status\|logs` | 管理 systemd 用户服务 |
| `nju-connect check` | 查看网络位置、VPN、服务和规则集状态 |
| `nju-connect upgrade` | 升级到最新版本 |
| `nju-connect uninstall [--purge]` | 删除服务和程序；`--purge` 同时删除配置和登录状态 |

如果已有 zju-connect 在运行，或代理端口被占用，`connect`、`login` 和 `service install` 会显示进程号并提示先停止它（`--force` 可跳过检查）。后台连接的日志在 `~/.local/state/nju-connect/zju-connect.log`。

## 配置

设置保存在两个文件中（zju-connect 只接受它认识的配置项，所以 nju-connect 自己的设置单独存放），可以统一用 `nju-connect config` 查看和修改：

```bash
nju-connect config show                          # 列出全部设置及所在文件（密码显示为 ********）
nju-connect config get daemon.check_interval
nju-connect config set daemon.check_interval 30
nju-connect config set clash.health_url http://lib.nju.edu.cn/
nju-connect config set account.password          # 不写值时会提示输入（密码不回显）
```

修改后会自动生效：涉及 Clash 的设置会重新生成已安装的 Clash Verge 脚本，涉及连接或后台服务的设置会重启正在运行的服务。非法的值会被拒绝，文件保持不变。

| 设置 | 含义 | 默认值 |
|---|---|---|
| `account.username` / `account.password` / `account.login_domain` | 学号、密码、登录域 | `setup` 时填写 |
| `server.address` / `server.port` | aTrust 服务器 | `vpn.nju.edu.cn` / `443` |
| `proxy.socks_port` / `proxy.http_port` | 本机代理端口（仅监听 127.0.0.1） | `1080` / `1081` |
| `daemon.check_interval` | 后台服务检查网络的间隔（秒，≥10） | `60` |
| `daemon.ruleset_interval` | 更新规则集的间隔（秒，≥300） | `1800` |
| `clash.proxy_name` / `clash.group_name` | Clash 代理和策略组名称 | `NJUConnect` / `NJU` |
| `clash.group_type` | 策略组类型：`fallback`、`url-test`、`select` | `fallback` |
| `clash.health_url` / `clash.health_interval` | 策略组健康检查地址（需能通过 VPN 访问）和间隔（秒，≥30） | `http://lib.nju.edu.cn/` / `300` |
| `clash.script`、`ruleset.output`、`ruleset.provider_path` | 脚本和规则集路径 | 根据检测到的 Clash 自动设置 |
| `campus.dns_servers` / `campus.probe_name` | 用于判断是否在校园网的内网 DNS 和查询域名 | `10.12.253.4, 10.28.253.4` / `www.nju.edu.cn` |

不需要填写手机号：南大使用密码登录，需要短信验证时 zju-connect 会从服务器获取手机号。

## 后台服务如何工作

`nju-connect service install` 会创建 `~/.config/systemd/user/nju-connect.service`，每分钟检查一次：

- **是否在校园网**：直接向南大内网 DNS（10.12.253.4、10.28.253.4）查询。只有在校园网内才会得到应答。
- **校外**：启动 zju-connect；通过 SOCKS5 代理向内网 DNS 查询来检查 VPN 是否可用，连续 3 次失败则重启；VPN 可用时每 30 分钟更新规则集。
- **校内**：停止 zju-connect。
- **需要登录**：还没有登录状态，或登录状态过期、服务器要求短信验证码时，服务不会反复重试（每次重试都会发送一条短信），而是弹出桌面通知并等待。运行 `nju-connect login` 完成登录后，服务会自动重新连接。
- **其他连接失败**：逐渐延长重试间隔（最长 30 分钟）。
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
- **提示 zju-connect 版本过旧**：生成规则集需要 `--fetch-resource` 选项，目前只有 [Huaji-tye2007/zju-connect](https://github.com/Huaji-tye2007/zju-connect) 的版本支持。运行 `nju-connect upgrade` 安装。

## 开发

源代码位于 [`nju_connect/`](nju_connect) 包中，只依赖标准库（Python 3.8+）：

| 模块 | 内容 |
|---|---|
| `cli.py` | 命令行参数和各子命令入口 |
| `configure.py` | `setup` 向导和 `config show/get/set` |
| `config.py` | 两个配置文件的读写和设置项定义（类型、校验、修改后的影响） |
| `paths.py` | 文件位置，查找 zju-connect |
| `network.py` | 校园网检测、VPN 健康检查、运行中实例检测 |
| `zju.py` | 调用 zju-connect（下载访问策略、获取登录方式） |
| `ruleset.py` | 把访问策略转换为 mihomo 规则集 |
| `clash.py` | 生成 Clash Verge 全局脚本 / mihomo 配置片段 |
| `daemon.py` | 后台服务的主循环 |
| `service.py` | systemd 用户服务 |

- 测试：`python3 -m unittest discover -s tests -t .`
- 直接运行源码：`python3 -m nju_connect --help`
- 打包：`tools/build.sh` 用标准库 `zipapp` 把整个包打成单个可执行文件 `dist/nju-connect`，Releases 中发布的就是它
- 从源码安装：克隆本仓库后运行 `./install.sh`，会先打包再安装
- 发布：修改 `nju_connect/__init__.py` 中的 `VERSION`，提交后推送 `v<VERSION>` 标签，GitHub Actions 会运行测试、打包并创建 Release

## 致谢

- [zju-connect](https://github.com/Mythologyli/zju-connect)（基于 [EasierConnect](https://github.com/lyc8503/EasierConnect)）：VPN 客户端本体，本项目使用其 [分支](https://github.com/Huaji-tye2007/zju-connect)
- [simonzxm/nju-connect](https://github.com/simonzxm/nju-connect)：南大配置和使用方式的参考

## 许可证

[GPL-3.0](LICENSE)
