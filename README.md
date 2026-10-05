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
| `daemon.campus_proxy`                                            | 在校内（zju-connect 停止时）由 nju-connect 在代理端口上提供直连代理：`direct` 或 `off` | `direct`                                      |
| `campus.dns_servers` / `campus.probe_name`                       | 用于判断是否在校园网的内网 DNS（`auto`：取自访问策略）和查询域名                     | `auto` / `www.nju.edu.cn`                     |
| `export.proxy_name` / `export.group_name`                        | 导出的 Clash/Xray 配置中的代理和策略组名称                                           | `NJUConnect` / `NJU`                          |
| `export.group_type`                                              | Clash 策略组类型：`fallback`、`url-test`、`select`                                   | `fallback`                                    |
| `export.health_url` / `export.health_interval`                   | Clash 策略组健康检查地址（需能通过 VPN 访问）和间隔（秒，≥30）                       | `http://lib.nju.edu.cn/` / `300`              |
| `export.resolve_domains`                                         | 为匹配学校 IP 段而解析的域名（逗号分隔；`*` 表示全部解析，留空表示从不解析），见下文 | `nju.edu.cn`                                  |

## 与代理工具配合

**最简单的方式**：连接后直接把应用、浏览器或系统代理设置为 `127.0.0.1:1080`（SOCKS5）或 `127.0.0.1:1081`（HTTP）。zju-connect 会根据学校的访问策略自行分流：南大资源走 VPN，其余直连。

**已经在用代理工具**时，用 `nju-connect export` 生成对应客户端的规则，只把南大流量交给 zju-connect。规则按学校下发的访问策略精确生成（域名、端口、TCP/UDP）。各客户端的支持情况如下，详细步骤见 [docs/proxy-clients.md](docs/proxy-clients.md)：

| 客户端                         | 命令                                                         | 需要手动做的事                                             | 学校策略变化后               |
| ------------------------------ | ------------------------------------------------------------ | ---------------------------------------------------------- | ---------------------------- |
| Clash Verge Rev                | `nju-connect export clash-verge --install`                   | 无（按提示重启一次 Clash Verge）                           | 自动                         |
| FlClash                        | `nju-connect export clash-verge --inline -o ~/nju-flclash.js` | 在“工具 → 进阶设置 → 脚本”中导入该文件，并在配置的覆写中启用 | 重新导入                     |
| Clash Party 等其他 mihomo 客户端 | `nju-connect export clash-verge --inline -o …` 或 `clash-config --inline -o …` | Clash Party：在“覆写”中导入脚本并打开全局启用；其他客户端：粘贴脚本或 YAML 片段 | 重新导入或粘贴               |
| 原生 mihomo                    | `nju-connect export clash -o ~/.config/mihomo/ruleset/nju-vpn.yaml` 和 `clash-config` | 把片段合并进 `config.yaml`（一次）                         | 自动                         |
| sing-box                       | `nju-connect export sing-box -o …` 和 `sing-box-config`      | 把出站和路由规则合并进配置（一次）                         | 自动                         |
| Xray                           | `nju-connect export xray --install -o ~/.config/xray/config.json` | 无（Xray 作为系统服务运行时需手动重启）                    | 自动合并并重启 Xray 用户服务 |
| v2rayN（Xray / sing-box 内核） | `nju-connect export v2rayn -o ~/nju-v2rayn.json`             | 导入一次节点链接；在路由设置中导入规则文件；mihomo 内核不支持 | 重新导入规则文件             |
| v2rayA                         | `nju-connect export list`                                    | **不直接支持**：按列表手写 RoutingA 规则                   | 手动修改                     |
| 浏览器 / 系统代理              | `nju-connect export pac -o ~/nju.pac`                        | 设置 PAC 地址 `file:///home/<用户名>/nju.pac`（一次）      | 自动                         |

各导出格式：

| 格式              | 内容                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------ |
| `clash-verge`     | Clash Verge Rev 全局扩展脚本（注入代理、`fallback` 策略组和规则）；`--install` 直接安装      |
| `clash`           | mihomo 规则集（rule-provider，classical）                                                  |
| `clash-config`    | mihomo 配置片段：代理、策略组、规则集和规则                                                |
| `sing-box`        | sing-box 规则集源文件（JSON）                                                              |
| `sing-box-config` | sing-box 出站和路由规则片段（引用上面的规则集；未导出时内联），需要 sing-box 1.11+         |
| `xray`            | Xray 出站和路由规则（JSON）；`--install` 合并进 Xray 配置文件（先备份、可重复执行）并重启 Xray |
| `v2rayn`          | v2rayN 可导入的路由规则列表：南大规则在前，后接当前启用规则集中原有的规则                  |
| `pac`             | PAC 文件：南大资源走 `127.0.0.1:1081`，其余直连                                            |
| `list`            | 纯文本列表（目标、端口、协议），可自行转换为其他格式                                       |

使用 `-o` 写入的文件（以及 `--install` 合并的 Xray 配置）会被记住，后台服务更新访问策略时会自动重新生成：

```bash
nju-connect export --list            # 查看已记住的导出文件
nju-connect export --forget sing-box # 不再自动更新（文件保留）
nju-connect export clash --refresh   # 先重新下载访问策略
```

**按 IP 段匹配的南大网站**：学校的访问策略中有不少网站只以 IP 段出现（例如 `xk.nju.edu.cn`、`ehall.nju.edu.cn` 属于 `219.219.112.0/20`，`lms.nju.edu.cn` 属于 `202.119.32.0/19`），zju-connect 会解析域名后按 IP 走 VPN。为了让代理工具也这样处理，导出的规则对 `export.resolve_domains` 中的域名（默认 `nju.edu.cn`）做了特殊处理，其他域名不受影响：

- Clash/mihomo：每个 IP 段生成 `IP-CIDR,…,no-resolve`（直接访问 IP 时）和 `AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,…))`（只解析南大域名）两条规则
- sing-box：`sing-box-config` 先按域名匹配规则集，再对南大域名执行 `resolve` 后按 IP 匹配
- Xray / v2rayN：在南大规则之后加一条 `domain:nju.edu.cn` 规则，把其余南大域名整体交给 zju-connect，由它通过 VPN 用校园网 DNS 解析，再按访问策略决定走 VPN 还是直连。这样代理客户端不需要自己解析域名（在校外只能用公网 DNS），也不用修改域名解析策略
- PAC：只对南大域名调用 `dnsResolve`

所有格式都会让 VPN 服务器和节点地址（如 `219.219.118.25`）直连，避免开启 TUN 模式时 zju-connect 自己的连接被转发回自身。

**在校内**：`auto` 模式在校内会停止 zju-connect，此时 nju-connect 自己在同样的 SOCKS5/HTTP 端口上提供一个直连代理（`daemon.campus_proxy`），因此把南大流量交给 `127.0.0.1:1080` 的规则在校内也能正常使用。导出的 Clash 策略组还是 `fallback` 类型：VPN 在线时走 zju-connect，不可用时自动改为直连。mihomo 只能读取其主目录下的规则集文件，因此当 `clash` 导出文件不在 Clash Verge Rev 或 `~/.config/mihomo` 目录中时，`clash-verge` / `clash-config` 会把规则直接写进脚本或片段。

## 后台服务如何工作

服务每分钟检查一次：

- **是否在校园网**（`auto` 模式）：直接（不经过 VPN）向南大内网 DNS 查询 `www.nju.edu.cn`。这些服务器使用 10.x 私有地址，只有在校园网内才能访问：有应答说明在校内；在校外，请求会发往家里的路由器而超时（每台 2 秒），说明在校外；完全没有网络时直接报错，视为离线。校外启动 zju-connect，校内停止，并改由 nju-connect 在代理端口上提供直连代理（同样监听 SOCKS5 和 HTTP 端口，用校园网 DNS 直接连接；`daemon.campus_proxy = off` 可关闭），离开校园网时先关闭它再启动 zju-connect；`always` 模式下始终连接。
  - 内网 DNS 地址默认（`campus.dns_servers = auto`）从访问策略中自动获取：策略中授权给 VPN 用户、开放 UDP 53 端口的私有地址（目前是 10.12.253.4、10.28.253.4），以及服务器下发的 DNS 设置。学校更换地址后，服务每 30 分钟更新一次访问策略时会自动跟上；还没有下载过访问策略时使用内置的这两个地址。公网 DNS 在校外也会应答，因此不会被采用。`nju-connect service status` 会显示当前使用的地址及来源，也可以用 `nju-connect config set campus.dns_servers 地址,地址` 手动指定。
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
- **开启代理工具的 TUN 模式后服务误判为在校内**：TUN 会把内网 DNS 查询也转发进 VPN。请在 TUN 设置中排除 `nju-connect service status` 中“campus check”显示的地址（目前是 10.12.253.4、10.28.253.4），或改用系统代理。
- **提示 zju-connect 版本过旧**：访问策略下载需要 `--fetch-resource` 选项，目前只有 [Huaji-tye2007/zju-connect](https://github.com/Huaji-tye2007/zju-connect) 的版本支持。运行 `nju-connect upgrade` 安装。
- **不想使用 systemd ?**：把 `nju-connect service run` 加入桌面自启动即可。
- **`service start` 和 `service enable` 的区别**：`start` 只启动这一次，重新登录或重启后不会自动运行；`enable` 会在每次登录系统时自动启动（并立即启动）。`service status` 中显示 `starts automatically` 即表示已启用。服务随用户登录启动；如果希望开机后、登录前就运行，可执行 `loginctl enable-linger $USER`（此时登录前看不到桌面通知）。

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
| `xray.py`      | Xray/v2rayN 相关格式，合并 Xray 配置并重启 Xray            |
| `direct.py`    | 在校内代替 zju-connect 提供的直连 SOCKS5/HTTP 代理         |

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
