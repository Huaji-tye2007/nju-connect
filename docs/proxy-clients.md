# 在各种代理客户端中使用 NJU Connect

NJU Connect 连接后，zju-connect 在本机提供两个代理：

- SOCKS5：`127.0.0.1:1080`（支持 UDP）
- HTTP：`127.0.0.1:1081`

（端口可用 `nju-connect config set proxy.socks_port …` 修改，下文以默认值为例；导出的规则和链接总是使用你实际设置的端口。）

zju-connect 自己会按学校的访问策略分流：南大资源走 VPN，其他流量直接从本机连出。因此有两种用法：

1. **不用代理客户端**：把浏览器或应用的代理直接设为上面的地址（见[第一节](#一不使用代理客户端)）。
2. **已经在用代理客户端**（Clash、sing-box、Xray、v2rayN 等）：把 zju-connect 作为一个出站/节点加入客户端，再用 `nju-connect export` 生成的规则只把南大流量交给它，其他流量照常走你原来的规则。

`nju-connect export` 用 `-o` 写入文件后会被记住，后台服务每 30 分钟更新访问策略时会自动重新生成；`nju-connect export --list` 查看，`--forget 格式` 取消（文件保留）。

**在校内**：`auto` 模式在校内会停止 zju-connect（校内不需要 VPN），此时 nju-connect 自己在同样的两个端口上提供一个**直连代理**：收到的请求直接从本机连出，域名用校园网 DNS 解析。所以无论你用哪种客户端、规则把南大流量交给 `127.0.0.1:1080`，在校内也能正常打开南大网站。离开校园网时，服务先关闭直连代理，再启动 zju-connect。不需要时可以 `nju-connect config set daemon.campus_proxy off`；`nju-connect service status` 会显示当前是 VPN 还是直连。

## 支持情况一览

| 客户端                                                            | 做法                                                | 需要手动做的事                                                        | 学校策略变化后               | 测试情况                                  |
| ----------------------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------- | ---------------------------- | ----------------------------------------- |
| [Clash Verge Rev](#clash-verge-rev)                               | `export clash-verge --install` 写入全局扩展脚本     | 无（按提示重启一次 Clash Verge）                                      | 自动，立即生效               | 已实测（新的文件结构待在界面中确认）      |
| [FlClash](#flclash)                                               | 导入 `--inline` 导出的覆写脚本                      | 导入                                                                  | 重新导入脚本                 | 内核已实测，界面步骤已实测                |
| [Clash Party 等](#clash-party--mihomo-party-等其他-mihomo-客户端) | 导入覆写脚本（其他客户端：脚本或 YAML 片段）        | 导入脚本并全局启用                                                    | 重新导入                     | Clash Party 已实测；其他客户端未实测      |
| [原生 mihomo](#原生-mihomo--自己维护的-configyaml)                | `export clash-config --install` 写入规则和代理文件  | 把打印的片段合并进 `config.yaml`（一次）                              | 自动，立即生效（包括改端口） | 已实测（mihomo 1.19）                     |
| [sing-box](#三sing-box-内核的客户端sing-box-111)                  | `export sing-box-config --install` 合并进配置并重载 | 无（sing-box 由 systemd 系统服务运行时需手动重载）                    | 自动，立即生效               | 已实测（sing-box 1.14）；图形客户端未实测 |
| [Xray](#xray原生内核)                                             | `export xray --install` 合并进配置并重启 Xray       | 无（Xray 由 systemd 系统服务运行时需手动重启）                        | 自动合并并重启用户服务       | 已实测（Xray 26.7）                       |
| [v2rayN](#v2rayn)                                                 | 导入节点链接 + 从文件导入路由规则                   | 导入一次节点；导入规则文件（Xray / sing-box 内核；mihomo 内核不支持） | 重新导入规则文件             | 已实测（Xray 内核，界面导入步骤）         |
| [v2rayA](#v2raya不直接支持)                                       | 手写 RoutingA                                       | 全部手动                                                              | 手动修改                     | 不直接支持，未实测                        |
| [浏览器 / 系统代理](#一不使用代理客户端)                          | PAC 文件                                            | 设置一次 PAC 地址                                                     | 自动（扩展中需重新粘贴）     | 已实测                                    |

> 关于“已实测”：各格式的规则已在 mihomo、sing-box 1.14、Xray 26.3 / 26.7 内核上用真实的访问策略实测（南大网站走 NJU、其他网站不受影响、VPN 服务器和节点直连）。各图形客户端的菜单名称和位置随版本变化，标为“未实测”的界面步骤是按客户端源码整理的，请以实际界面为准；遇到问题欢迎提 issue。

## 先了解：只以 IP 段出现的南大网站

学校的访问策略中有不少网站只以 IP 段出现（例如 `xk.nju.edu.cn`、`ehall.nju.edu.cn` 属于 `219.219.112.0/20`）。zju-connect 收到域名后会自己解析（先查访问策略，再通过 VPN 查询校园网 DNS），然后按 IP 走 VPN；代理客户端如果只按域名规则匹配，这些网站会漏掉。导出的规则用两种办法处理 `nju.edu.cn` 下的域名（`export.resolve_domains` 设置），其他域名不受影响：

- **Clash/mihomo、sing-box、PAC**：代理客户端先解析南大域名，再匹配 IP 段。在校外，这次解析用的是客户端的 DNS（公网），只能看到公网的解析结果。
- **Xray、v2rayN**：在南大规则之后加一条 `domain:nju.edu.cn` 规则，把其余南大域名**原样**交给 zju-connect，由它用校园网 DNS 解析并按访问策略分流（策略之外的地址由 zju-connect 直接连出）。客户端不需要自己解析，也不用改域名解析策略。

另外，导出的规则都会让 VPN 服务器（`vpn.nju.edu.cn`）和 VPN 节点地址直连。**这几条规则必须放在你的规则最前面**，否则开启 TUN 模式时，zju-connect 自己连接 VPN 的流量会被转发回它自己。

**怎样确认生效**：在校外、VPN 已连接时，通过代理客户端访问只以 IP 段出现的 `http://xk.nju.edu.cn`（选课系统）和普通的 `https://www.baidu.com`。前者能打开、后者正常，就说明南大流量走了 VPN、其他流量没受影响。命令行可以用 `curl -I -x socks5h://127.0.0.1:<客户端端口> http://xk.nju.edu.cn`。

## 一、不使用代理客户端

先导出 PAC 文件：

```bash
nju-connect export pac -o ~/nju.pac
```

PAC 只把南大资源交给 `127.0.0.1:1081`，其余直连；zju-connect 停止时自动直连。

- **Firefox**：设置 → 网络设置 → 自动代理配置 URL，填 `file:///home/<用户名>/nju.pac`。文件更新后点“重新载入”。
- **浏览器扩展**（ZeroOmega / SwitchyOmega 等）：扩展通常不能读取 `file://` 地址，请新建一个“PAC 情景模式”，把 `~/nju.pac` 的内容粘贴到 PAC 脚本框中（学校策略变化后需要重新粘贴）。
- **系统代理**（GNOME：设置 → 网络 → 网络代理 → 自动，配置 URL 填 `file:///home/<用户名>/nju.pac`）。注意：如果你的代理客户端会接管系统代理，二者只能选一个，此时请改用下面对应客户端的方法。
- **命令行**：`curl -x socks5h://127.0.0.1:1080 https://lib.nju.edu.cn`，或 `export ALL_PROXY=socks5h://127.0.0.1:1080`。因为 zju-connect 自己会分流，非南大地址也能正常访问。

## 二、mihomo（Clash Meta）内核的客户端

导出的 Clash 配置包括：

- 代理 `NJUConnect`：`socks5 127.0.0.1:1080`，支持 UDP，以及它的直连备用项 `NJUConnect-DIRECT`
- 策略组 `NJU`：`fallback` 类型，先用 NJUConnect，每 5 分钟通过它访问 `http://lib.nju.edu.cn/` 检测；VPN 不可用（例如服务停止）时自动改为直连
- 两个规则集：`nju-direct`（VPN 服务器和节点，必须直连）和 `nju-vpn`（南大资源），以及放在最前面的两条规则 `RULE-SET,nju-direct,DIRECT`、`RULE-SET,nju-vpn,NJU`

所有 mihomo 客户端得到的结构都相同（名称、策略组和规则一致），区别只在代理和规则集放在哪里：不加 `--install`（FlClash、Clash Party 用的 `--inline` 脚本，以及打印的片段）时直接写在脚本或片段里（`type: inline`），学校策略变化后需要重新导入；用 `--install` 安装时（Clash Verge Rev、原生 mihomo）放在 nju-connect 维护的文件里：

| 文件（相对 mihomo 的主目录） | 内容                                         |
| ---------------------------- | -------------------------------------------- |
| `ruleset/nju-vpn.yaml`       | 南大资源                                     |
| `ruleset/nju-direct.yaml`    | VPN 服务器和节点                             |
| `proxies/nju-connect.yaml`   | `NJUConnect`（含端口）和 `NJUConnect-DIRECT` |

后台服务更新访问策略、或你修改 SOCKS 端口时只重写这些文件；mihomo 会监视它们的变化，**立即生效，不需要重启**。只有修改代理名称、策略组名称、类型或检测地址（`nju-connect config set export.…`）时，策略组和规则本身才会变化。

### Clash Verge Rev

**已实测。** 全自动，不需要在界面中操作。（这一版把规则和代理改放到上表的文件中，这个新结构还需要在 Clash Verge Rev 界面中再确认一次。）

1. 运行：

   ```bash
   nju-connect export clash-verge --install
   ```

   这会写入 Clash Verge Rev 的全局扩展脚本 `profiles/Script.js`（原脚本会自动备份），并把上表的三个文件写到 Clash Verge 的数据目录下。

2. Clash Verge Rev 只在重新生成配置时（启动时，或在界面中修改设置后）运行全局扩展脚本，不会因为脚本文件变化而自动生效。因此脚本有变化且 Clash Verge 正在运行时，命令会询问是否重启 Clash Verge（会以原来的命令行和桌面环境重新启动，代理内核在服务模式下继续运行）。不重启的话，也可以在 Clash Verge 中打开全局扩展脚本、随便修改一下再保存。
3. 确认：“规则”页面中应能看到 `nju-direct` 和 `nju-vpn`，“代理”页面中有 `NJU` 策略组，成员依次是 `NJUConnect`、`NJUConnect-DIRECT`。

**学校策略变化后**：后台服务只更新上表的文件，mihomo 立即重新读取，不需要重启 Clash Verge。只有修改 `export.*` 的名称或策略组设置时脚本才会变化，这时会弹出桌面通知提醒你重启 Clash Verge。

**移除**：在 Clash Verge 中清空全局扩展脚本（或换回备份的 `Script.js.bak-…`），然后运行 `nju-connect export --forget clash-verge`。

### FlClash

**内核和 FlClash 界面步骤均已实测。**（这一版的脚本改为与 Clash Verge Rev 相同的结构，代理放在 `type: inline` 的 proxy-provider 中，已在 mihomo 1.19 内核上实测，还需要在 FlClash 中再确认一次。）FlClash 0.8.85 起支持与 Clash Verge Rev 相同的 `main(config)` 覆写脚本。FlClash 读不到 Clash Verge 目录中的规则集文件，因此导出时把规则直接写进脚本：

1. 导出脚本：

   ```bash
   nju-connect export clash-verge --inline -o ~/nju-flclash.js
   ```

2. 在 FlClash 中打开“工具” → “进阶设置” → “脚本”，在右上角“添加”中选择“从文件中导入”，选择 `~/nju-flclash.js` 进行导入。
3. 然后在左侧边栏的“配置”中选中你将要使用的配置文件，点击配置卡片右上角的三个圆点，选择“更多” → “覆写” → “脚本”，选中刚刚导入的脚本，它左侧的空心圆点变为实心圆点即表示已启用。
4. 按上文“怎样确认生效”检查。

**学校策略变化后**：规则写在脚本内部，FlClash 不会自动读取更新后的文件。`nju-connect export --list` 可以看到文件的更新时间；文件更新后重新导入一次即可。

### Clash Party / Mihomo Party 等其他 mihomo 客户端

**Clash Party 已实测；其他图形客户端均未实测。**（这一版的脚本结构有变化，同 FlClash，还需要在 Clash Party 中再确认一次。）

**Clash Party**（JavaScript 覆写脚本）：

1. 导出脚本：

   ```bash
   nju-connect export clash-verge --inline -o ~/nju-clash.js
   ```

2. 点击左侧的“覆写”，再点击右上角的“+” → “打开”，选择 `~/nju-clash.js`。
3. 打开这个覆写的“全局启用”滑块。
4. 按上文“怎样确认生效”检查。

**学校策略变化后**：规则写在脚本内部，需要重新导入一次 `~/nju-clash.js`（`nju-connect export --list` 可以看到文件的更新时间）。

**其他 mihomo 客户端**（Clash Nyanpasu 等，未实测）：

- 如果客户端支持 JavaScript 覆写脚本（`function main(config) { … return config }`），做法同上：把 `nju-connect export clash-verge --inline -o ~/nju-clash.js` 导出的内容导入或粘贴为一个覆写脚本并启用。
- 如果只支持 YAML 合并/覆写，导出配置片段：

  ```bash
  nju-connect export clash-config --inline -o ~/nju-clash.yaml
  ```

  把其中的 `proxies`、`proxy-groups`、`rule-providers` 手动加入你的配置，`rules` 中的几条放在你的规则**最前面**。

学校策略变化后同样需要重新导入或粘贴。

### 原生 mihomo / 自己维护的 config.yaml

**已实测**（mihomo 1.19：按打印的提示合并后，`mihomo -t` 通过，策略组成员顺序正确，替换规则文件和代理文件后立即生效）。

nju-connect 不会修改你的 `config.yaml`（Python 标准库无法可靠地解析和改写 YAML），而是把会变化的内容都放进上表的文件里。所以只需要**手动合并一次**：

1. 写入文件，并打印需要合并的内容：

   ```bash
   nju-connect export clash-config --install
   ```

   命令会从正在运行的 mihomo 进程找到它的主目录，找不到时使用 `~/.config/mihomo`；也可以用 `-o 主目录`（或 `-o 主目录/config.yaml`）指定。主目录属于 root（例如系统服务的 `/etc/mihomo`）时 nju-connect 无法写入，请改用你自己目录下的配置运行 mihomo，或用 `-o` 指定一个可写的主目录。

2. 按打印的四段提示，把内容合并进 `config.yaml`。打印的内容分为 ①②③④ 四段，每段注明放在哪个键下面；没有这个键时新建即可。合并后大致如下（具体参数可能因设置的不同而发生变化，具体以生成的参数为准）：

   ```yaml
   proxy-providers:
     nju-connect:
       { type: file, path: ./proxies/nju-connect.yaml, interval: 600 }
   proxy-groups:
     - {
         name: NJU,
         type: fallback,
         use: [nju-connect],
         url: "http://lib.nju.edu.cn/",
         interval: 300,
       }
   rule-providers:
     nju-direct:
       {
         type: file,
         behavior: classical,
         format: yaml,
         path: ./ruleset/nju-direct.yaml,
         interval: 600,
       }
     nju-vpn:
       {
         type: file,
         behavior: classical,
         format: yaml,
         path: ./ruleset/nju-vpn.yaml,
         interval: 600,
       }
   rules:
     - RULE-SET,nju-direct,DIRECT
     - RULE-SET,nju-vpn,NJU
     # …你原来的规则
   ```

   策略组用 `use: [nju-connect]` 引用代理文件，不要再写 `proxies: [NJUConnect, DIRECT]`：mihomo 会把 `proxies` 排在 `use` 之前，`fallback` 就会先选到直连。

3. 重新加载一次 mihomo（重启，或通过它的 API `PUT /configs`）。

**之后**：学校策略变化、修改 SOCKS 端口都会自动生效，不需要再做任何事。修改 `export.*` 的名称或策略组设置后，后台服务会弹出通知；这时重新运行第 1 步，按提示更新 ② 和 ④ 两段。

**从旧的做法迁移**（以前用 `export clash -o …` 加 `export clash-config` 合并过）：运行第 1 步，用新的四段替换以前合并的 `NJUConnect` 代理、`NJU` 策略组、`nju-vpn` 规则集和最前面的几条规则，再运行 `nju-connect export --forget clash`。

**只想手动合并**：`nju-connect export clash-config`（不加 `--install`）打印一份结构相同、内容全部内联的片段（代理、VPN 节点直连规则、南大资源都写在其中），学校策略变化后需要重新合并。

## 三、sing-box 内核的客户端（sing-box 1.11+）

**已实测**（sing-box 1.14：合并、`sing-box check`、南大网站走 `NJUConnect`、VPN 服务器直连、收到 SIGHUP 后重新加载）。适用于自己维护配置文件的 sing-box。

1. 合并并重载 sing-box：

   ```bash
   nju-connect export sing-box-config --install
   ```

   命令会从正在运行的 sing-box 进程（`-c`、`-C`、`-D` 参数和工作目录）找到配置文件，找不到时查找 `~/.config/sing-box/config.json`、`/etc/sing-box/config.json`；也可以用 `-o 配置文件` 指定。它会：
   - 第一次合并前备份原文件（`config.json.bak-时间`）；合并后的文件是标准 JSON，**原文件中的注释不会保留**
   - 在配置文件旁的 `nju-connect/` 目录中写入三个 `local` 规则集：`nju-direct`（VPN 服务器和节点）、`nju-vpn`（南大资源）、`nju-resolve`（需要解析后再按 IP 匹配的南大域名）
   - 在 `outbounds` **末尾**加入 `NJUConnect`，不影响你的默认出站；直连规则使用你已有的 `direct` 类型出站，没有时追加一个
   - 在 `route.rules` **最前面**加入四条引用上述规则集的规则；再次合并时只替换引用这些规则集的规则，不会重复添加
   - 能找到 `sing-box` 命令（`PATH` 中，或环境变量 `NJU_CONNECT_SING_BOX`）时，先用 `sing-box check` 检查，检查失败则不修改原文件
   - 由 systemd **用户**服务运行时执行 `systemctl --user reload-or-restart`；手动运行的 sing-box 发送 SIGHUP 让它重新加载；系统服务提示你运行 `sudo systemctl reload-or-restart …`

   合并后的规则大致如下：

   ```json
   {
     "rules": [
       { "rule_set": "nju-direct", "outbound": "direct" },
       { "rule_set": "nju-vpn", "outbound": "NJUConnect" },
       { "rule_set": "nju-resolve", "action": "resolve" },
       { "rule_set": "nju-vpn", "outbound": "NJUConnect" }
     ]
   }
   ```

   `resolve` 需要 `route.default_domain_resolver` 指向一个可用的 DNS 服务器，没有设置时命令会给出提示。nju-connect 不会修改 `route.final` 和 `default_domain_resolver`。

2. 按上文“怎样确认生效”检查，命令行代理端口为你的 sing-box 入站端口。

**学校策略变化后**：后台服务只更新 `nju-connect/` 中的规则集，sing-box 会监视它们的变化，立即生效，不需要重载。修改 SOCKS 端口或代理名称时，后台服务会重新合并配置并重载 sing-box。

**配置文件属于 root**（例如发行版软件包的 `/etc/sing-box/config.json`）时 nju-connect 无法写入，做法同下文 Xray 一节：改用你自己目录下的配置和 systemd 用户服务运行 sing-box。

**移除**：运行 `nju-connect export --forget sing-box-config`，再用备份文件恢复，或删除引用 `nju-*` 规则集的规则、这三个规则集和 `NJUConnect` 出站。

**只想手动合并**：`nju-connect export sing-box -o 文件` 写出规则集，`nju-connect export sing-box-config`（不加 `--install`）打印需要合并的出站和路由规则（VPN 节点直连规则写在其中，学校策略变化后需要重新合并）。

**图形客户端**（GUI.for.SingBox、NekoBox、Hiddify 等，未实测）：在其“自定义出站/路由规则/规则集”设置中按上面的方式添加；如果客户端只能把规则指向它自己的节点，可以先把 `socks5 127.0.0.1:1080` 添加为一个节点，再让南大规则指向该节点。

## 四、Xray / V2Ray 内核的客户端

Xray 按“入站 → 路由 → 出站”处理每个连接：路由规则从上到下匹配，命中的规则用 `outboundTag` 决定交给哪个出站，都没命中就交给第一个出站。nju-connect 做的是：

- 加一个出站 `NJUConnect`（`socks` → `127.0.0.1:1080`），放在出站列表**末尾**，你原来的默认出站不变
- 在路由规则**最前面**加几条规则：VPN 服务器和节点 → `direct`，访问策略中的域名和 IP 段 → `NJUConnect`，最后是 `domain:nju.edu.cn` → `NJUConnect`（其余南大域名，见上文“先了解”）
- 不修改 `routing.domainStrategy`：上面的规则在 `AsIs`、`IPIfNonMatch`、`IPOnDemand` 下都能正确匹配。以前版本的 nju-connect 合并时会把它改成 `IPOnDemand`，现在可以改回你原来的设置（只有 `export.resolve_domains` 设为 `*` 时才需要 `IPOnDemand`）

### Xray（原生内核）

**已实测**（Xray 26.7，`domainStrategy: AsIs`）。适用于自己维护 `config.json` 的 Xray。

1. 合并规则并重启 Xray：

   ```bash
   nju-connect export xray --install
   ```

   命令会从正在运行的 Xray 进程（`-c`、`-confdir` 参数和工作目录）找到配置文件，找不到时依次查找 `~/.config/xray/config.json`、`/usr/local/etc/xray/config.json`、`/etc/xray/config.json`；也可以用 `-o 配置文件` 指定。v2rayN 自带的 Xray 不会被选中（见下文 v2rayN 一节）。这个命令会：
   - 第一次合并前备份原文件（`config.json.bak-时间`）。注意：合并后的文件是标准 JSON，**原文件中的注释不会保留**，注释请到备份中找
   - 加入上面说的出站和规则（带 `"ruleTag": "nju-connect"`，再次合并时只替换这些规则，不会重复添加）；如果配置中没有 tag 为 `direct` 的出站，会追加一个 `freedom` 出站
   - 如果能找到 `xray` 命令（`PATH` 中，或环境变量 `NJU_CONNECT_XRAY`），先用 `xray run -test` 检查合并结果，检查失败则不修改原文件
   - 如果 Xray 由 systemd **用户**服务运行，询问是否重启这个服务；如果是系统服务，提示你运行 `sudo systemctl restart …`

2. 如果 Xray 是官方安装脚本装的系统服务，配置文件 `/usr/local/etc/xray/config.json` 属于 root，nju-connect 无法写入。可以改用用户服务运行 Xray：把配置复制到 `~/.config/xray/config.json`，`sudo systemctl disable --now xray`，再创建 `~/.config/systemd/user/xray.service`：

   ```ini
   [Unit]
   Description=Xray (user)
   After=network-online.target

   [Service]
   ExecStart=/usr/local/bin/xray run -c %h/.config/xray/config.json
   Restart=on-failure

   [Install]
   WantedBy=default.target
   ```

   然后运行 `systemctl --user enable --now xray`，再执行第 1 步。（需要 TUN 或 1024 以下端口时，用户服务没有足够权限，请继续使用系统服务，并在每次规则更新后手动重启。）

3. 按上文“怎样确认生效”检查，命令行代理端口为你的 Xray 入站端口。

**学校策略变化后**：后台服务重新合并规则，并自动重启 Xray 用户服务；不能自动重启时（系统服务、手动运行的 Xray）会弹出桌面通知提醒你重启。

**移除**：运行 `nju-connect export --forget xray`，再用备份文件恢复，或删除配置中带 `"ruleTag": "nju-connect"` 的规则和 `NJUConnect` 出站。

**只想手动合并**：`nju-connect export xray`（不加 `--install`）打印 `outbounds` 和 `routing`，按上面的说明自己合并即可。

### v2rayN

v2rayN 是图形界面：它把你添加的节点和“路由设置”中的规则保存在自己的数据库里，每次启动或切换节点时按**活动节点的内核类型**生成内核配置（`binConfigs/config.json`）并运行内核。因此**不能直接修改它生成的配置文件**（会被覆盖），要通过它的界面添加：

- 一个 SOCKS 节点，别名为 `NJUConnect`，指向 zju-connect
- 当前路由规则集中的南大规则，出站写节点别名 `NJUConnect`（v2rayN 7.x 起，规则的出站可以是任意节点的别名）

**状态**：已在 v2rayN 7.24（Xray 内核）的界面中按下面的步骤实测。下面菜单名称来自 v2rayN 7.24 的中文界面。

1. **导出规则文件**：

   ```bash
   nju-connect export v2rayn -o ~/nju-v2rayn.json
   ```

   文件内容是 v2rayN“导入规则”所需的 JSON 规则列表：先是南大规则（别名都以 `nju-connect:` 开头），然后是你**当前启用的规则集**中原有的全部规则（nju-connect 只读取 v2rayN 的数据库，不会修改它；以前导入的 `nju-connect:` 规则会被去掉，不会重复）。命令会打印下面几步要用的节点链接和规则集名称。

   > 之前用 `nju-connect export xray` 的输出导入会失败：那是 Xray 配置片段（一个对象），而 v2rayN 需要的是规则列表。

2. **添加节点**（只需一次）：复制命令打印的链接（例如 `socks://Og@127.0.0.1:1080#NJUConnect`），在 v2rayN 主界面选择“配置项” → “从剪贴板导入分享链接”。列表中会多出一个别名为 `NJUConnect` 的 SOCKS 节点。**不要把它设为活动节点**，活动节点仍是你平时用的节点。

   也可以手动添加：“配置项” → “添加 [SOCKS]”，地址 `127.0.0.1`，端口 `1080`，别名 `NJUConnect`。**别名必须与规则中的出站完全一致**：v2rayN 找不到这个别名时，会把这些规则静默地改为走当前节点（`proxy`）。

3. **导入规则**：“设置” → “路由设置”，在规则集列表中双击当前启用的那个规则集（命令打印的名称，例如“V4-绕过大陆(Whitelist)”），打开“规则集设置”窗口：
   1. 点“从文件中导入规则”，选择 `~/nju-v2rayn.json`（也可以复制文件内容后点“从剪贴板中导入规则”）
   2. 提示“是否追加规则？”时选**“否”（全部替换）**：文件中已经包含这个规则集原有的规则，替换后南大规则在最前面，其余规则顺序不变
   3. 点“确定”关闭“规则集设置”，再点“确定”关闭“路由设置”，v2rayN 会重新生成配置并重启内核

   不需要修改“域名解析策略”：没有出现在访问策略中的南大域名由最后一条 `nju-connect: other nju.edu.cn hosts` 规则整体交给 zju-connect 解析。

   如果命令提示没找到 v2rayN 的数据（例如 v2rayN 装在别处，可用环境变量 `NJU_CONNECT_V2RAYN_DIR` 指定它的数据目录），文件中就只有南大规则：导入时选“是”（追加），再在规则列表中用“上移至顶”把这些 `nju-connect:` 规则移到最前面。

4. **确认**：规则列表最前面应是 `nju-connect: VPN server direct` 等规则。然后按上文“怎样确认生效”检查，命令行代理端口为 v2rayN 的本地端口（默认 `10808`）。

**注意**：

- v2rayN 同一时间只启用一个规则集。切换到别的规则集后，需要在那个规则集中重新导入（先重新运行第 1 步，它会读取新的启用规则集）。
- 开启 TUN 模式时，zju-connect 自己的连接也会进入 v2rayN；导入的前两条规则让 VPN 服务器和节点直连，因此不会形成回环。
- 在校内，规则仍然把南大流量交给 `127.0.0.1:1080`，由 nju-connect 的直连代理直接连出（见开头“在校内”）。

**内核**：v2rayN 按活动节点的内核类型生成配置（“设置” → “参数设置” → “Core 类型设置”中可为各类节点选择内核）：

- **Xray 内核**（默认）：已在 v2rayN 界面中实测，规则也在 Xray 26.7 内核上单独实测。
- **sing-box 内核**：v2rayN 会把同一份规则转换为 sing-box 规则（精确域名、正则、端口、TCP/UDP 和节点别名都会保留），按 v2rayN 7.24 源码确认，**未实测**。不要把“路由设置”窗口中的全局“域名解析策略”设为 `IPOnDemand`：在 sing-box 内核下，这会让所有连接都先用 v2rayN 的 DNS 解析再按 IP 连接，交给 zju-connect 的也就不再是域名。
- **mihomo 内核**：v2rayN 只在“自定义配置”（导入完整的 Clash 配置）时使用 mihomo，此时**路由设置中的规则完全不起作用**，nju-connect **不支持**这种用法。请改用 Clash Verge Rev 等 mihomo 客户端（见第二节），或把这份 Clash 配置换成普通节点。

**学校策略变化后**：后台服务会重新生成 `~/nju-v2rayn.json`（`nju-connect export --list` 可看到更新时间），但 v2rayN 不会自动读取，需要重复第 3 步（导入时同样选“否”）。

**移除**：在规则列表中选中所有 `nju-connect:` 规则后“移除所选规则”，删除 `NJUConnect` 节点，再运行 `nju-connect export --forget v2rayn`。

### v2rayA（不直接支持）

v2rayA 没有导入规则文件的功能，nju-connect 也没有为它生成配置，**只能手动配置，未实测**。v2rayA 的分流用 RoutingA 语法（“设置” → “路由” → RoutingA）：

1. 用 `nju-connect export list` 查看全部目标地址、端口和协议，以及需要直连的 VPN 节点。
2. 在 RoutingA 中定义一个指向 zju-connect 的出站，再把南大的域名和 IP 段指向它，放在其他规则前面。例如：

   ```
   outbound: njuconnect=socks(address: 127.0.0.1, port: 1080)
   domain(full:vpn.nju.edu.cn)->direct
   ip(219.219.118.25)->direct
   domain(full:lib.nju.edu.cn, regexp:^.+\.example\.com$)->njuconnect
   ip(219.219.112.0/20, 202.119.32.0/19)->njuconnect
   ```

   有端口或协议限制的条目可以写成 `ip(…) && port(80,443) && network(tcp)->njuconnect`；具体语法请参考 v2rayA 文档。

3. 学校策略变化后需要按新的 `export list` 输出手动修改。

## 五、其他工具

`nju-connect export list` 输出纯文本列表（目标、端口、协议，以及需要直连的 VPN 节点），可以据此转换为任何工具的规则格式。也欢迎提交 PR 增加新的导出格式（见 `nju_connect/exporters.py`）。
