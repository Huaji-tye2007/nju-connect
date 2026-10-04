# NJU Connect CLI

基于 [zju-connect](https://github.com/Mythologyli/zju-connect) 的南京大学 aTrust VPN Linux 命令行工具

- 一条命令安装，账号密码单独保存在权限为 600 的配置文件中
- 后台服务自动判断是否在校园网：校外自动连接 VPN，回到校内自动断开；也可设为始终连接
- 可以根据学校下发的访问策略，导出 Clash/mihomo、sing-box、Xray/V2Ray 规则或 PAC 文件，并自动保持最新
- 连接后在本机提供 SOCKS5 / HTTP 代理服务，南大资源走 VPN ，其余直连

## 安装

需要 Linux环境、`curl` 和 Python 3.8+。

```bash
curl -fsSL https://raw.githubusercontent.com/Huaji-tye2007/nju-connect-cli/main/install.sh | bash
```

安装程序会从 [Huaji-tye2007/zju-connect](https://github.com/Huaji-tye2007/zju-connect/releases) 下载对应架构的 `zju-connect`（没有预编译文件且装有 Go 时从源码编译）、从本仓库 [Releases](https://github.com/Huaji-tye2007/nju-connect-cli/releases) 下载 `nju-connect`，安装到 `~/.local/bin`，然后运行 `nju-connect setup`。

运行 `nju-connect upgrade`（或重新运行安装命令）即可升级，已有配置不会被覆盖，后台服务会自动重启。可用 `NJU_CONNECT_VERSION=<tag>` / `ZJU_CONNECT_VERSION=<tag>` 安装指定版本，`NJU_CONNECT_PREFIX=<目录>` 修改安装位置。

## 首次使用

`nju-connect setup`（安装程序会自动运行）依次完成：

1. 询问学号、密码、登录方式，以及是否修改默认代理端口（SOCKS5 1080 / HTTP 1081，直接回车保持默认）
2. **首次登录**：在当前终端运行 zju-connect，需要时输入短信验证码；登录成功、保存登录状态后自动退出
3. 询问是否启用后台服务（登录系统后自动运行）

之后后台服务会复用保存的登录状态，不需要再输入验证码。登录状态过期时，服务会弹出桌面通知并等待，运行 `nju-connect login` 重新登录即可。

## 使用方式

zju-connect 只由后台服务运行，所有控制都在 `nju-connect service` 下：

```bash
nju-connect service status    # 网络位置、VPN、代理、登录状态、服务状态
nju-connect service start     # 启动（auto 模式下只在校外连接）
nju-connect service stop      # 停止
nju-connect service restart
nju-connect service logs      # 查看日志
nju-connect service enable    # 登录系统后自动启动（并立即启动）
nju-connect service disable   # 取消自动启动（并停止）
nju-connect service run       # 在当前终端运行（没有 systemd 的系统可把它加入自启动）
```

终端只在 `nju-connect login` 时使用（输入短信验证码）；后台服务运行时，`login` 会自动暂停服务，登录完成后再恢复。`service start` / `enable` / `restart` 会先检查保存的登录状态（只用已保存的 Cookie 询问服务器，不会触发短信）；如果已经过期，会先在当前终端完成登录再启动服务，避免服务启动后因需要短信验证码而无法连接。

## 命令

| 命令                                | 作用                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- |
| `nju-connect setup [--advanced]`    | 首次配置：账号、端口、首次登录、后台服务；`--advanced` 还会询问连接模式、检查间隔和校园网检测设置 |
| `nju-connect login`                 | 在终端中登录（需要时输入短信验证码）并保存登录状态                                                |
| `nju-connect service ...`           | 控制后台服务，见上文                                                                              |
| `nju-connect config show\|get\|set` | 查看或修改单项设置，见下文「配置」                                                                |
| `nju-connect export ...`            | 为代理工具导出规则，见下文「与代理工具配合」                                                      |
| `nju-connect trust` / `untrust`     | 把本机设为授信终端 / 取消授信（授信后登录免短信）                                                 |
| `nju-connect upgrade`               | 升级到最新版本                                                                                    |
| `nju-connect uninstall [--purge]`   | 先取消本机的授信（`--keep-trust` 跳过），再删除服务和程序；`--purge` 同时删除配置和登录状态       |

## 配置

设置保存在两个文件中（zju-connect 只接受它认识的配置项，所以 nju-connect 自己的设置单独存放），可以统一用 `nju-connect config` 查看和修改：

```bash
nju-connect config show                       # 列出全部设置及所在文件（密码显示为 ********）
nju-connect config set daemon.mode always     # 在校内也保持连接
nju-connect config set daemon.check_interval 30
nju-connect config set account.password       # 不写值时会提示输入（密码不回显）
```

修改后会自动生效：涉及连接或服务的设置会重启正在运行的服务，涉及导出的设置会重新生成已记住的导出文件。非法的值会被拒绝，文件保持不变。

| 设置                                                             | 含义                                                                                 | 默认值                                        |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------- |
| `account.username` / `account.password` / `account.login_domain` | 学号、密码、登录域                                                                   | `setup` 时填写                                |
| `server.address` / `server.port`                                 | aTrust 服务器                                                                        | `vpn.nju.edu.cn` / `443`                      |
| `proxy.socks_port` / `proxy.http_port`                           | 本机代理端口（仅监听 127.0.0.1）                                                     | `1080` / `1081`                               |
| `daemon.mode`                                                    | `auto`：只在校外连接；`always`：始终连接                                             | `auto`                                        |
| `daemon.check_interval`                                          | 检查网络的间隔（秒，≥10）                                                            | `60`                                          |
| `daemon.ruleset_interval`                                        | 更新访问策略和导出文件的间隔（秒，≥300）                                             | `1800`                                        |
| `campus.dns_servers` / `campus.probe_name`                       | 用于判断是否在校园网的内网 DNS 和查询域名                                            | `10.12.253.4, 10.28.253.4` / `www.nju.edu.cn` |
| `export.proxy_name` / `export.group_name`                        | 导出的 Clash/Xray 配置中的代理和策略组名称                                           | `NJUConnect` / `NJU`                          |
| `export.group_type`                                              | Clash 策略组类型：`fallback`、`url-test`、`select`                                   | `fallback`                                    |
| `export.health_url` / `export.health_interval`                   | Clash 策略组健康检查地址（需能通过 VPN 访问）和间隔（秒，≥30）                       | `http://lib.nju.edu.cn/` / `300`              |
| `export.resolve_domains`                                         | 为匹配学校 IP 段而解析的域名（逗号分隔；`*` 表示全部解析，留空表示从不解析），见下文 | `nju.edu.cn`                                  |

## 与代理工具配合

**最简单的方式**：连接后直接把应用、浏览器或系统代理设置为 `127.0.0.1:1080`（SOCKS5）或 `127.0.0.1:1081`（HTTP）。zju-connect 会根据学校的访问策略自行分流：南大资源走 VPN，其余直连。

**已经在用代理工具**（Clash、sing-box、Xray 等，需要只把南大流量交给 zju-connect）时，用 `nju-connect export` 生成对应格式的规则。规则按学校下发的访问策略精确生成（域名、端口、TCP/UDP），使用 `-o` 写入文件后会被记住，后台服务更新访问策略时会自动重新生成：

| 格式              | 用途                                                                                         | 示例                                                                                                                                                         |
| ----------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `clash-verge`     | Clash Verge Rev 全局扩展脚本（注入代理、`fallback` 策略组和规则）                            | `nju-connect export clash-verge --install`，然后在 Clash Verge 中重新加载订阅                                                                                |
| `clash`           | mihomo 规则集（rule-provider，classical）                                                    | `nju-connect export clash -o ~/.config/mihomo/ruleset/nju-vpn.yaml`                                                                                          |
| `clash-config`    | mihomo 配置片段：代理、策略组、规则集和规则，适合 FlClash、Mihomo Party 等其他 mihomo 客户端 | `nju-connect export clash-config`，把输出合并进配置                                                                                                          |
| `sing-box`        | sing-box 规则集源文件（JSON），适合 sing-box、Hiddify、GUI.for.SingBox                       | `nju-connect export sing-box -o ~/nju-vpn.json`                                                                                                              |
| `sing-box-config` | sing-box 出站和路由规则片段（引用上面的规则集；未导出时内联），需要 sing-box 1.11+           | `nju-connect export sing-box-config`，把 `outbounds`、`route.rule_set` 和 `route.rules` 合并进配置（需要有 `direct` 出站和 `route.default_domain_resolver`） |
| `xray`            | Xray/V2Ray 出站和路由规则（JSON），适合 v2rayA、Xray                                         | `nju-connect export xray`，把 `outbounds`、`routing.rules` 和 `routing.domainStrategy` 合并进配置                                                            |
| `pac`             | PAC 文件：南大资源走 `127.0.0.1:1081`，其余直连                                              | `nju-connect export pac -o ~/nju.pac`，在浏览器或系统代理中设置 `file:///home/<用户名>/nju.pac`                                                              |
| `list`            | 纯文本列表（目标、端口、协议），可自行转换为其他格式                                         | `nju-connect export list`                                                                                                                                    |

```bash
nju-connect export --list            # 查看已记住的导出文件
nju-connect export --forget sing-box # 不再自动更新（文件保留）
nju-connect export clash --refresh   # 先重新下载访问策略
```

**按 IP 段匹配的南大网站**：学校的访问策略中有不少网站只以 IP 段出现（例如 `xk.nju.edu.cn`、`ehall.nju.edu.cn` 属于 `219.219.112.0/20`，`lms.nju.edu.cn` 属于 `202.119.32.0/19`），zju-connect 会解析域名后按 IP 走 VPN。为了让代理工具也这样处理，导出的规则会解析 `export.resolve_domains` 中的域名（默认 `nju.edu.cn`）再匹配 IP 段，其他域名不会因此多一次 DNS 查询：

- Clash/mihomo：每个 IP 段生成 `IP-CIDR,…,no-resolve`（直接访问 IP 时）和 `AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,…))`（只解析南大域名）两条规则
- sing-box：`sing-box-config` 先按域名匹配规则集，再对南大域名执行 `resolve` 后按 IP 匹配
- Xray：设置 `domainStrategy: IPOnDemand`。Xray 无法只解析部分域名，因此所有域名都会在经过这些规则时被解析一次
- PAC：只对南大域名调用 `dnsResolve`

所有格式都会让 VPN 服务器和节点地址（如 `219.219.118.25`）直连，避免开启 TUN 模式时 zju-connect 自己的连接被转发回自身。

导出的 Clash 策略组为 `fallback` 类型：VPN 在线时走 zju-connect，zju-connect 停止时（例如在校内）自动改为直连。mihomo 只能读取其主目录下的规则集文件，因此当 `clash` 导出文件不在 Clash Verge Rev 或 `~/.config/mihomo` 目录中时，`clash-verge` / `clash-config` 会把规则直接写进脚本或片段。

## 后台服务如何工作

服务每分钟检查一次：

- **是否在校园网**（`auto` 模式）：直接向南大内网 DNS（10.12.253.4、10.28.253.4）查询，只有在校园网内才会得到应答。校外启动 zju-connect，校内停止；`always` 模式下始终连接。
- **VPN 是否可用**：通过 SOCKS5 代理向内网 DNS 查询，连续 3 次失败则重启 zju-connect。
- **访问策略**：VPN 可用时每 30 分钟更新一次，并重新生成已记住的导出文件。
- **需要登录**：还没有登录状态，或登录状态过期、服务器要求短信验证码时，服务不会反复重试（每次重试都会发送一条短信），而是弹出桌面通知并等待，直到 `nju-connect login` 保存新的登录状态。
- **其他连接失败**：逐渐延长重试间隔（最长 30 分钟）。

## 文件位置

| 路径                                                   | 内容                                 |
| ------------------------------------------------------ | ------------------------------------ |
| `~/.local/bin/zju-connect`、`~/.local/bin/nju-connect` | 程序                                 |
| `~/.config/nju-connect/config.toml`                    | zju-connect 配置，含密码（权限 600） |
| `~/.config/nju-connect/nju-connect.conf`               | nju-connect 设置和已记住的导出文件   |
| `~/.local/state/nju-connect/client_data.json`          | 登录状态                             |
| `~/.local/state/nju-connect/resource.json`             | 最近一次下载的访问策略               |
| `~/.config/systemd/user/nju-connect.service`           | 后台服务                             |

## 常见问题

- **每次都要短信验证码**：运行 `nju-connect trust` 把本机设为授信终端。学校限制每个账号最多 3 台电脑、3 台手机；超过时会失败（错误码 75500311），需要先在其他设备上取消授信。
- **开启代理工具的 TUN 模式后服务误判为在校内**：TUN 会把内网 DNS 查询也转发进 VPN。请在 TUN 设置中排除 10.12.253.4、10.28.253.4，或改用系统代理。
- **提示 zju-connect 版本过旧**：访问策略下载需要 `--fetch-resource` 选项，目前只有 [Huaji-tye2007/zju-connect](https://github.com/Huaji-tye2007/zju-connect) 的版本支持。运行 `nju-connect upgrade` 安装。
- **不想使用 systemd ?**：把 `nju-connect service run` 加入桌面自启动即可。

## 参与开发

源代码位于 [`nju_connect/`](nju_connect) 包中，只依赖标准库（Python 3.8+）：

| 模块           | 内容                                                       |
| -------------- | ---------------------------------------------------------- |
| `cli.py`       | 命令行参数和各子命令入口                                   |
| `configure.py` | `setup` 向导、`login` 和 `config show/get/set`             |
| `config.py`    | 两个配置文件的读写和设置项定义（类型、校验、修改后的影响） |
| `paths.py`     | 文件位置，查找 zju-connect                                 |
| `service.py`   | systemd 用户服务                                           |
| `daemon.py`    | 服务主循环（`service run`）                                |
| `network.py`   | 校园网检测、VPN 健康检查、运行中实例检测                   |
| `zju.py`       | 调用 zju-connect（交互式登录、下载访问策略、获取登录方式） |
| `policy.py`    | 把访问策略解析为与工具无关的条目                           |
| `exporters.py` | 各种导出格式和已记住的导出文件                             |
| `clash.py`     | Clash/mihomo 相关格式                                      |

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

## 参与测试

**极其欢迎提交issue和PR！！！**
由于本项目当前仅为个人开发，在不同 Linux 发行版、不同桌面环境、不同代理工具下可能存在各种问题，欢迎大家提供使用反馈。目前本人使用的环境为 Ubuntu 24.04，代理工具为 Clash Verge Rev，其他环境可能存在兼容性问题。请在提交 issue 时提供以下信息：

- Linux 发行版及版本号
- 桌面环境（GNOME、KDE、XFCE 等）
- 代理工具及版本号（Clash、sing-box、Xray 等）
- 具体问题描述（包括命令行输出、日志、截图等）
- 如何复现问题

希望大家能积极参与使用和反馈，让这个工具更加完善和易用！
