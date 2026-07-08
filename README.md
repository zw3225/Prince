# 海外趋势爆款雷达数据看板

一个可运行的第一版数据看板：输入趋势类目、目标人群或场景后，后端优先采集无需密钥的公开页面真实信号，前端展示爆款内容、垂类达人、热销/强营销产品和机会洞察。

当前版本把 connector、评分、API 和看板体验跑通。真实平台接入时，只需要替换 `backend/connectors.py` 中的 connector 实现，API 字段和前端展示可以保持稳定。

## 运行

最省事的方式是直接双击：

```text
start-dashboard.bat
```

脚本会自动打开：

```text
http://127.0.0.1:8787
```

如果浏览器显示“拒绝连接”，说明本机服务没有启动成功，先确认黑色启动窗口还开着，并且浏览器地址不是 `8000`。

也可以手动运行：

```powershell
python backend/server.py
```

然后打开：

```text
http://127.0.0.1:8787
```

如果本机没有把 Python 放进 PATH，可以使用 Codex 工作区依赖里的 Python 路径运行。

## 让团队访问

当前看板默认只监听 `127.0.0.1`，也就是只有本机能访问。要让别人看到，有两种方式：

### 0. 最省事：同事本地运行一份

如果公司网络或 Windows 防火墙不允许同事访问你的电脑，可以把整个项目文件夹压缩发给同事。同事解压后双击：

```text
start-dashboard.bat
```

然后打开：

```text
http://127.0.0.1:8787
```

这种方式不需要改你电脑防火墙，也不需要同事连你的电脑。详细说明见 `TEAM_USE.md`。

### 1. 同一办公室 / 同一 Wi-Fi 内访问

在你的电脑上用局域网模式启动：

```powershell
$env:TREND_RADAR_HOST = "0.0.0.0"
$env:TREND_RADAR_PORT = "8787"
python backend/server.py
```

然后把你的电脑局域网 IP 发给同事，例如：

```text
http://你的电脑IP:8787
```

如果别人打不开，通常是 Windows 防火墙没有放行 8787 端口，或大家不在同一个网络里。

### 2. 所有人都能通过固定网址访问

把这套服务放到一台长期在线的服务器、公司内网机器或云主机上，再绑定域名或内网地址。启动方式同上：

```powershell
$env:TREND_RADAR_HOST = "0.0.0.0"
$env:TREND_RADAR_PORT = "8787"
python backend/server.py
```

对外共享时建议放在公司 VPN、内网网关或带登录保护的反向代理后面，不建议把没有登录保护的采集看板直接暴露到公网。

## API

- `POST /api/trend-searches`
- `GET /api/trend-searches/{id}/summary`
- `GET /api/rankings/content?search_id=...`
- `GET /api/rankings/creators?search_id=...`
- `GET /api/rankings/products?search_id=...`
- `GET /api/entities/{type}/{id}?search_id=...`
- `GET /api/sources/health`

## 测试

```powershell
python -m unittest discover -s tests
```

## 接入真实数据

后端现在支持真实来源优先，而且不强依赖数据密钥：

- 默认模式等同于 `TREND_RADAR_DATA_MODE=real`：只看真实来源；没有抓到可直达的真实链接时返回空结果和状态提示，不再自动展示样例数据。
- `TREND_RADAR_DATA_MODE=real`：只看真实来源；没有配置、公开页面被拦截或抓取失败时返回空结果和状态提示。
- `TREND_RADAR_DATA_MODE=demo`：只看样例数据，适合演示页面交互。

已接入的真实来源：

- YouTube：无密钥时解析 YouTube 公开结果页中真实出现的 `videoId` 和频道入口；如果配置 `YOUTUBE_API_KEY`，优先使用官方 API。内容直达视频详情，达人直达频道主页。
- Amazon：无密钥时解析公开结果页中真实出现的 ASIN；产品直达 `/dp/{ASIN}` 商品详情。
- eBay：无密钥时解析公开结果页中真实出现的 `/itm/` 商品链接；如果配置 `EBAY_BROWSE_TOKEN`，优先使用 Browse API。
- Etsy：无密钥时解析公开结果页中真实出现的 listing 链接；如果配置 `ETSY_API_KEY`，优先使用 Open API。
- Reddit：无密钥时尝试公开 JSON；如平台拒绝，会显示失败原因，不生成假链接。若未来有 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`，会优先走 OAuth。

无密钥运行示例：

```powershell
$env:TREND_RADAR_DATA_MODE = "real"
python backend/server.py
```

没有从公开页面或 API 中解析到真实详情 URL 时，页面不会拼搜索页、不会猜链接，也不会给假链接。
