# 在各种代理客户端中使用 NJU Connect

NJU Connect 连接后，zju-connect 在本机提供两个代理：

- SOCKS5：`127.0.0.1:1080`（支持 UDP）
- HTTP：`127.0.0.1:1081`

（端口可用 `nju-connect config set proxy.socks_port …` 修改，下文以默认值为例。）

zju-connect 自己会按学校的访问策略分流：南大资源走 VPN，其他流量直接从本机连出。因此有两种用法：

1. **不用代理客户端**：把浏览器或应用的代理直接设为上面的地址（见[第一节](#一不使用代理客户端)）。
2. **已经在用代理客户端**（Clash、sing-box、Xray 等）：把 zju-connect 作为一个出站/节点加入客户端，再用 `nju-connect export` 生成的规则只把南大流量交给它，其他流量照常走你原来的规则。

`nju-connect export` 用 `-o` 写入文件后会被记住，后台服务每 30 分钟更新访问策略时会自动重新生成；`nju-connect export --list` 查看，`--forget 格式` 取消。

> 关于下文的“已实测”：各格式的规则已在 mihomo、sing-box 1.14、Xray 26.3 内核上用真实的访问策略实测（南大网站走 NJU、其他网站不受影响、VPN 服务器和节点直连）。各图形客户端的菜单名称和位置随版本变化，**没有逐一实测**，请以客户端文档为准；遇到问题欢迎提 issue。

## 先了解：为什么要“解析南大域名”

学校的访问策略中有不少网站只以 IP 段出现（例如 `xk.nju.edu.cn`、`ehall.nju.edu.cn` 属于 `219.219.112.0/20`）。zju-connect 会解析域名后按 IP 走 VPN；代理客户端如果不解析域名，就只能按域名规则匹配，这些网站会漏掉（通常被后面的“国内直连”规则带走）。

导出的规则默认只解析 `nju.edu.cn` 下的域名（`export.resolve_domains` 设置），其他域名不会因此多一次 DNS 查询。Xray 无法只解析部分域名，会解析所有经过这些规则的域名。

另外，导出的规则都会让 VPN 服务器（`vpn.nju.edu.cn`）和 VPN 节点地址直连。**这几条规则必须放在你的规则最前面**，否则开启 TUN 模式时，zju-connect 自己连接 VPN 的流量会被转发回它自己。

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

- 代理 `NJUConnect`：`socks5 127.0.0.1:1080`，支持 UDP
- 策略组 `NJU`：`fallback` 类型，`[NJUConnect, DIRECT]`，每 5 分钟通过 NJUConnect 访问 `http://lib.nju.edu.cn/` 检测；VPN 不可用（例如在校内、服务停止）时自动改为直连
- 规则集 `nju-vpn` 和放在最前面的几条规则（VPN 服务器和节点直连，然后 `RULE-SET,nju-vpn,NJU`）

名称、策略组类型和检测地址可以用 `nju-connect config set export.…` 修改。

### Clash Verge Rev（已实测）

```bash
nju-connect export clash-verge --install
```

这会写入 Clash Verge Rev 的全局扩展脚本 `profiles/Script.js`（原脚本会自动备份），并把规则集写到 Clash Verge 目录下的 `ruleset/nju-vpn.yaml`。

Clash Verge Rev 只在重新生成配置时（启动时，或在界面中修改设置后）运行全局扩展脚本，不会因为脚本文件变化而自动生效。因此脚本有变化且 Clash Verge 正在运行时，命令会询问是否重启 Clash Verge（会以原来的命令行和桌面环境重新启动，代理内核在服务模式下继续运行）。不重启的话，也可以在 Clash Verge 中打开全局扩展脚本、随便修改一下再保存。生效后，“规则”页面中应能看到 `nju-vpn`。

之后学校策略变化时，后台服务只会更新规则集文件；规则集设置了 `interval: 600`，mihomo 每 10 分钟会自动重新读取，不需要重启 Clash Verge。

### FlClash（脚本格式与 Clash Verge Rev 相同，FlClash 中未实测）

FlClash 0.8.85 起支持与 Clash Verge Rev 相同的 `main(config)` 覆写脚本（“配置”页右上角 → 脚本）。FlClash 读不到 Clash Verge 目录中的规则集文件，因此导出时把规则直接写进脚本：

```bash
nju-connect export clash-verge --inline -o ~/nju-flclash.js
```

把 `~/nju-flclash.js` 的内容粘贴为新脚本并启用。规则写在脚本内部，FlClash 不会自动读取更新后的文件；学校策略变化后（`nju-connect export --list` 可看到更新时间），重新粘贴一次即可。

### Mihomo Party / Clash Nyanpasu 等其他 mihomo 客户端（未实测）

- 如果客户端支持 JavaScript 覆写脚本（`function main(config) { … return config }`），做法同 FlClash。
- 如果只支持 YAML 合并/覆写，导出配置片段：

  ```bash
  nju-connect export clash-config --inline -o ~/nju-clash.yaml
  ```

  把其中的 `proxies`、`proxy-groups`、`rule-providers` 加入你的配置，`rules` 中的几条放在你的规则**最前面**。

### 原生 mihomo / 自己维护的 config.yaml（已实测）

规则集文件需要放在 mihomo 的主目录下（mihomo 只读取主目录内的规则集文件），这样可以自动更新：

```bash
nju-connect export clash -o ~/.config/mihomo/ruleset/nju-vpn.yaml
nju-connect export clash-config        # 打印需要合并的片段，规则集为 file 类型
```

合并后大致如下：

```yaml
proxies:
  - {name: NJUConnect, type: socks5, server: 127.0.0.1, port: 1080, udp: true}
proxy-groups:
  - {name: NJU, type: fallback, proxies: [NJUConnect, DIRECT], url: "http://lib.nju.edu.cn/", interval: 300}
rule-providers:
  nju-vpn: {type: file, behavior: classical, format: yaml, path: ./ruleset/nju-vpn.yaml}
rules:
  - DOMAIN,vpn.nju.edu.cn,DIRECT
  - IP-CIDR,219.219.118.25/32,DIRECT,no-resolve   # 以导出内容为准
  - RULE-SET,nju-vpn,NJU
  # …你原来的规则
```

规则集设置了 `interval: 600`，mihomo 每 10 分钟会重新读取文件，规则集更新后会自动生效。

## 三、sing-box 内核的客户端（sing-box 1.11+）

导出两样东西：

```bash
nju-connect export sing-box -o ~/.config/sing-box/nju-vpn.json   # 规则集源文件，自动更新
nju-connect export sing-box-config                                # 打印需要合并的出站和路由规则
```

`sing-box-config` 会引用上面记住的规则集文件（没有导出时把规则内联）。合并方法（已实测）：

- `outbounds` 中加入它给出的 `socks` 出站（tag 为 `NJUConnect`）；你的配置需要有一个 tag 为 `direct` 的直连出站
- `route.rule_set` 中加入 `nju-vpn`
- 把 `route.rules` 中的几条放在你的路由规则**最前面**：先按域名匹配规则集；再对 `nju.edu.cn` 域名执行 `resolve`；然后按 IP 再匹配一次
- `route.default_domain_resolver` 需要指向一个可用的 DNS 服务器（`resolve` 动作要用到）

合并后大致如下：

```json
{
  "outbounds": [
    {"type": "direct", "tag": "direct"},
    {"type": "socks", "tag": "NJUConnect", "server": "127.0.0.1", "server_port": 1080, "version": "5"}
  ],
  "route": {
    "default_domain_resolver": "你的 DNS 服务器 tag",
    "rule_set": [{"type": "local", "tag": "nju-vpn", "format": "source", "path": "/home/<用户名>/.config/sing-box/nju-vpn.json"}],
    "rules": [
      {"domain": ["vpn.nju.edu.cn"], "outbound": "direct"},
      {"ip_cidr": ["219.219.118.25/32"], "outbound": "direct"},
      {"rule_set": "nju-vpn", "outbound": "NJUConnect"},
      {"domain_suffix": ["nju.edu.cn"], "action": "resolve"},
      {"rule_set": "nju-vpn", "outbound": "NJUConnect"}
    ]
  }
}
```

sing-box 会监视 `local` 规则集文件的变化，因此规则更新后会自动生效。

图形客户端（GUI.for.SingBox、NekoBox、Hiddify 等，未实测）：在其“自定义出站/路由规则/规则集”设置中按上面的方式添加；如果客户端只能把规则指向它自己的节点，可以先把 `socks5 127.0.0.1:1080` 添加为一个节点，再让南大规则指向该节点。

## 四、Xray / V2Ray 内核的客户端

```bash
nju-connect export xray
```

输出包含一个 `socks` 出站（tag 为 `NJUConnect`）和 `routing`（`domainStrategy` 与 `rules`）。合并方法（已实测）：

- `outbounds` 中加入该出站；你的配置需要有一个 tag 为 `direct` 的直连出站（`freedom`）
- `routing.rules` 中的几条放在你的规则**最前面**
- `routing.domainStrategy` 设为 `IPOnDemand`：这样在经过 IP 规则时会解析域名，`xk.nju.edu.cn` 这类只以 IP 段出现的网站才能匹配。不要用 `IPIfNonMatch`：后面的 `geosite:cn` 等域名规则会先匹配，南大网站就不会被解析
- 如果你的 DNS 服务器本身也经过路由，记得让它走直连

Xray 的配置文件不会自动重新读取，学校策略变化后需要重新导出并重启。

### v2rayN（未实测）

v2rayN 的路由规则界面通常只能把流量指向 `proxy`（当前节点）、`direct`、`block`。可行的方法：

- 使用 v2rayN 的“自定义配置”类型的服务器，导入一份合并了上面内容的完整 Xray 配置；或
- 浏览器侧使用第一节的 PAC 方法（让南大网站绕过 v2rayN，直接交给 zju-connect）。

### v2rayA（未实测）

v2rayA 的 RoutingA 路由需要在其中定义一个指向 `socks5://127.0.0.1:1080` 的出站，再把南大的域名和 IP 段指向它；具体语法请参考 v2rayA 文档。可以用 `nju-connect export list` 得到全部目标地址、端口和协议。

## 五、其他工具

`nju-connect export list` 输出纯文本列表（目标、端口、协议，以及需要直连的 VPN 节点），可以据此转换为任何工具的规则格式。也欢迎提交 PR 增加新的导出格式（见 `nju_connect/exporters.py`）。
