# 大麦网自动抢票脚本

基于 Chrome DevTools Protocol (CDP) 的大麦网浏览器端辅助脚本：连接**已登录**的 Chrome/Chromium，自动完成**导航、预选场次/票档、轮询购买按钮、提交订单**（支付需人工）。  


## 原理

```
Chrome/Chromium (已登录大麦) ◄── CDP WebSocket ──► 本机Python 脚本
            ▲                                        │
      用户可见页面                          预选场次/票档、点击购买、提交订单
```

**说明**：本项目**不包含**地图选座；若场次为「不支持选座」，行为与页面一致。部分场次仅限 **大麦 App** 购票，浏览器内会出现「该渠道不支持购票」等提示，脚本会检测后以 **exit code 2** 退出，无法用 CDP 替代原生 App。

## 环境要求

- Windows / macOS / Linux 均可（脚本端）
- Chrome 或 Chromium（建议 120+）
- Python 3.8+，依赖：`websockets`

默认**本机**连本机 Chrome；跨机器时自行用 SSH 把远端 `9222` 转到本机即可。

## 安装（应确认本机已安装python环境）

```bash
pip3 install websockets
```

## 使用步骤

### 第一步：启动 Chrome 远程调试

先**完全退出** Chrome，再启动（否则调试端口不生效）：

```bash
# macOS
open -a "Google Chrome" --args --remote-debugging-port=9222

# Windows（PowerShell 示例）
# & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# Linux
# google-chrome --remote-debugging-port=9222
```

### 第二步：登录大麦

在 Chrome 中打开 <https://www.damai.cn> 或 <https://m.damai.cn> 并登录。

### 第三步：配置文件

```bash
cp config.example.json config.json
```

编辑 `config.json`（勿将含真实场次 ID、开售时间的 `config.json` 提交到公开仓库，本仓库已将其列入 `.gitignore`）。

**完整字段说明**（与 `grab_ticket.py` 读取项一致）：

| 字段 | 说明 |
|------|------|
| `cdp_host` | CDP 地址，本机一般为 `127.0.0.1` |
| `cdp_port` | 调试端口，默认 `9222` |
| `mobile_mode` | `true` 时启用移动 UA、视口与触摸仿真；PC 详情 `detail.damai.cn/item.htm?id=` 会自动转为 `m.damai.cn/shows/item.html?itemId=` |
| `mobile_user_agent` | 可选；留空则用内置 iPhone Safari 风格 UA |
| `mobile_viewport_width` / `mobile_viewport_height` | 仿真分辨率，默认 `390` × `844` |
| `mobile_device_scale_factor` | 设备像素比，默认 `3` |
| `target_url` | 演出详情页 URL（从地址栏复制） |
| `target_time` | 开抢时间 `YYYY-MM-DD HH:MM:SS`；留空或删字段则**立即**进入抢票循环 |
| `session_index` | 场次索引，从 `0` 起 |
| `ticket_tier_index` | 票档索引，从 `0` 起 |
| `ticket_count` | 张数 |
| `viewer_names` | 观演人姓名；空数组 `[]` 表示由页面默认全选（若支持） |
| `poll_interval_ms` | 轮询间隔（毫秒） |
| `retry_count` | 最大重试轮数 |
| `random_delay_ms` | 刷新间隔随机延迟 `[最小, 最大]` 毫秒，降低风控风险 |

### 第四步：测试连接

```bash
python3 grab_ticket.py test   # 测 CDP 与当前标签页
python3 grab_ticket.py list   # 列出所有调试目标标签
```

### 第五步：运行

```bash
python3 grab_ticket.py
```

**可选：cron 到点只负责拉起进程**（须提前开好带调试端口的 Chrome 并已登录）：

- 使用本机时区，与 `target_time` 一致；任务内 `cd` 到项目目录，并用 `which python3` 的绝对路径。
- 日志目录需存在，例如：`mkdir -p logs`

```bash
# crontab -e 示例：每天 HH:MM 执行一次（请按开售日自行改五段）
# MM HH DD MM DOW
# 40 13 26 3 *  cd /path/to/damai-ticket && /usr/bin/python3 grab_ticket.py >> /path/to/damai-ticket/logs/cron.log 2>&1
```

## 脚本大致流程

1. 连接 CDP，优先选用已打开的大麦标签  
2. 按需导航到 `target_url`  
3. 若检测到仅 App 购票文案 → 退出码 `2`  
4. 预选场次、票档、数量  
5. 若配置了 `target_time` → 等待到时刻并刷新后再选  
6. 轮询「立即购买」等按钮，可点后点击并尝试提交订单  
7. 进入支付页后停止，**须人工支付**

## 注意事项

- 不自动支付；合规与风控风险自负，仅供学习研究  
- 页面改版可能导致选择器失效，需改 `grab_ticket.py` 内嵌 JS  
- `poll_interval_ms` 不宜过小（例如长期低于 `100`）  
- `mobile_mode` 不能绕过「仅限 App」策略  

## 仓库文件

```
├── grab_ticket.py      # 主程序
├── config.example.json # 配置模板（复制为 config.json）
├── .gitignore          # 忽略本地 config.json、logs 等
└── README.md
```

## License

MIT
