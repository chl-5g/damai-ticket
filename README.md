# 大麦网自动抢票脚本

基于 Chrome DevTools Protocol (CDP) 的大麦网自动抢票工具。通过连接 Mac 上已登录的 Chrome 浏览器，自动完成抢票操作。

## 原理

```
Mac Chrome (已登录大麦网) ◄── CDP WebSocket ──► 工作站 Python 脚本
       ▲                                              │
  用户看到操作过程                              自动点击/选座/下单
```

核心优势：**复用已登录的浏览器会话**，无需处理登录和验证码。利用 Chrome 的远程调试功能，脚本通过 WebSocket 直接操控浏览器页面。

## 环境要求

- **Mac 端**: Chrome 浏览器（建议 Chrome 120+）
- **工作站端**: Python 3.8+、`websockets` 库
- 两台机器在同一局域网内（或通过 SSH 端口转发连接）

## 安装

```bash
pip3 install websockets
```

## 使用步骤

### 第一步：Mac 启动 Chrome 远程调试

关闭所有 Chrome 窗口后执行：

```bash
open -a "Google Chrome" --args --remote-debugging-port=9222
```

> 如果 Chrome 已在运行，必须先完全退出再用此命令启动，否则 `--remote-debugging-port` 参数不会生效。

### 第二步：登录大麦网

在 Chrome 中打开大麦网 (https://www.damai.cn 或 https://m.damai.cn)，确保已登录账号。

### 第三步：建立 SSH 端口转发（工作站端）

```bash
ssh -L 9222:localhost:9222 用户名@Mac的IP地址
```

这样工作站的 `localhost:9222` 就会转发到 Mac Chrome 的调试端口。

### 第四步：测试连接

```bash
# 测试 CDP 连接是否正常
python3 grab_ticket.py test

# 列出 Chrome 中所有打开的标签页
python3 grab_ticket.py list
```

### 第五步：编辑配置

修改 `config.json`：

```json
{
    "cdp_host": "127.0.0.1",
    "cdp_port": 9222,
    "target_url": "https://m.damai.cn/shows/detail.html?itemId=演出ID",
    "target_time": "2026-03-20 10:00:00",
    "session_index": 0,
    "ticket_tier_index": 0,
    "ticket_count": 1,
    "viewer_names": ["张三"],
    "poll_interval_ms": 200,
    "retry_count": 50,
    "random_delay_ms": [50, 150]
}
```

**配置项说明**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `cdp_host` | CDP 连接地址 | `"127.0.0.1"` |
| `cdp_port` | CDP 端口 | `9222` |
| `target_url` | 大麦演出页面 URL | 从浏览器地址栏复制 |
| `target_time` | 开抢时间，留空则立即开始 | `"2026-03-20 10:00:00"` |
| `session_index` | 场次索引，从 0 开始 | `0` = 第一场 |
| `ticket_tier_index` | 票档索引，从 0 开始 | `0` = 第一档（通常最贵） |
| `ticket_count` | 购买张数 | `1` |
| `viewer_names` | 观演人姓名，空数组则全选 | `["张三", "李四"]` |
| `poll_interval_ms` | 轮询间隔（毫秒） | `200` |
| `retry_count` | 最大重试次数 | `50` |
| `random_delay_ms` | 随机延迟范围（毫秒），防风控 | `[50, 150]` |

### 第六步：运行抢票

```bash
python3 grab_ticket.py
```

## 抢票流程

1. **连接阶段** — 连接 Chrome CDP，自动找到大麦网页面标签
2. **准备阶段** — 导航到目标演出页，预选场次、票档、数量
3. **等待阶段** — 如果设置了 `target_time`，精确等待到开抢时刻
4. **抢票阶段** — 高频轮询购买按钮状态，变为可点击时立即点击
5. **下单阶段** — 自动选择观演人，提交订单
6. **支付阶段** — 进入支付页面后停止，**需要手动完成支付**

## 注意事项

- 脚本不会自动支付，进入支付页后需要手动操作
- 建议先用不热门的演出测试全流程
- 大麦网页面结构可能更新，如遇到元素定位失败需调整 JS 选择器
- `poll_interval_ms` 不宜设太小（< 100ms），可能触发风控
- 该脚本仅供学习研究使用

## 文件结构

```
├── grab_ticket.py   # 主脚本
├── config.json      # 配置文件
├── .gitignore
└── README.md
```

## License

MIT
