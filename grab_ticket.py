#!/usr/bin/env python3
"""
大麦网自动抢票脚本 — 基于 Chrome CDP 远程调试

使用方式:
1. Mac 启动 Chrome: open -a "Google Chrome" --args --remote-debugging-port=9222
2. 在 Chrome 中登录大麦网
3. 编辑 config.json（可选 `mobile_mode` 走 H5 移动仿真）
4. python3 grab_ticket.py

（可选）跨机器运行时再加 SSH 转发:
ssh -L 9222:localhost:9222 <user>@<mac_ip>
"""

import asyncio
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 移动模式默认 UA（iPhone Safari）；部分场次仅开放 H5，仍可能被服务端要求跳转 App
DEFAULT_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

try:
    import websockets
except ImportError:
    sys.exit("请安装 websockets: pip3 install websockets")

try:
    import urllib.request
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


class CDPClient:
    """轻量 Chrome DevTools Protocol 客户端"""

    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self._id = 0
        self._callbacks = {}

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=10 * 1024 * 1024)
        # 启动消息接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        try:
            async for msg in self.ws:
                data = json.loads(msg)
                msg_id = data.get("id")
                if msg_id and msg_id in self._callbacks:
                    self._callbacks[msg_id].set_result(data)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def send(self, method, params=None, timeout=30):
        self._id += 1
        msg_id = self._id
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params

        future = asyncio.get_event_loop().create_future()
        self._callbacks[msg_id] = future

        await self.ws.send(json.dumps(payload))
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._callbacks.pop(msg_id, None)

        if "error" in result:
            raise RuntimeError(f"CDP error: {result['error']}")
        return result.get("result", {})

    async def evaluate(self, expression, await_promise=False):
        params = {"expression": expression, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        result = await self.send("Runtime.evaluate", params)
        val = result.get("result", {})
        if val.get("type") == "undefined":
            return None
        if "value" in val:
            return val["value"]
        if val.get("subtype") == "error":
            raise RuntimeError(f"JS error: {val.get('description', val)}")
        return val

    async def close(self):
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            await self.ws.close()


def get_ws_url(host, port, target_url=None):
    """从 CDP /json 接口获取页面的 WebSocket 调试地址"""
    url = f"http://{host}:{port}/json"
    with urllib.request.urlopen(url, timeout=5) as resp:
        pages = json.loads(resp.read())

    if not pages:
        sys.exit("Chrome 没有打开任何页面")

    # 优先找大麦页面
    for page in pages:
        if page.get("type") != "page":
            continue
        page_url = page.get("url", "")
        if "damai.cn" in page_url:
            print(f"  找到大麦页面: {page_url[:80]}")
            return page["webSocketDebuggerUrl"]

    # 如果指定了 target_url，尝试匹配已打开的对应标签
    if target_url:
        for page in pages:
            if page.get("type") != "page":
                continue
            if target_url in page.get("url", ""):
                print(f"  匹配目标 URL 标签: {page.get('url', '')[:80]}")
                return page["webSocketDebuggerUrl"]

    # 回退到第一个页面
    for page in pages:
        if page.get("type") == "page":
            print(f"  未找到大麦页面，使用: {page.get('url', 'unknown')[:80]}")
            return page["webSocketDebuggerUrl"]

    sys.exit("无法找到可用的页面标签")


# ──────────────────────────────────────────────
# 大麦网页面操作 JS 代码
# 大麦移动端(m.damai.cn)和PC端(www.damai.cn)结构不同
# 以下同时支持两种情况
# ──────────────────────────────────────────────

JS_CHECK_BUY_BUTTON = """
(() => {
    // 移动端: 查找"立即购买"/"即将开抢"等按钮
    const btns = document.querySelectorAll('button, div[class*="buy"], div[class*="Book"], a[class*="buy"], span[class*="buy"]');
    for (const btn of btns) {
        const text = btn.textContent.trim();
        if (text.includes('立即购买') || text.includes('立即预订') || text.includes('选座购买')) {
            return {found: true, clickable: true, text: text};
        }
        if (text.includes('即将开抢') || text.includes('预售') || text.includes('暂时缺货') || text.includes('已售罄')) {
            return {found: true, clickable: false, text: text};
        }
    }
    // PC端
    const pcBtn = document.querySelector('.buybtn, .buy-btn, .buy-button, [class*="buyBtn"]');
    if (pcBtn) {
        const text = pcBtn.textContent.trim();
        const disabled = pcBtn.classList.contains('disabled') || pcBtn.getAttribute('disabled');
        return {found: true, clickable: !disabled, text: text};
    }
    return {found: false, clickable: false, text: ''};
})()
"""

JS_CLICK_BUY_BUTTON = """
(() => {
    const btns = document.querySelectorAll('button, div[class*="buy"], div[class*="Book"], a[class*="buy"], span[class*="buy"]');
    for (const btn of btns) {
        const text = btn.textContent.trim();
        if (text.includes('立即购买') || text.includes('立即预订') || text.includes('选座购买')) {
            btn.click();
            return 'clicked: ' + text;
        }
    }
    const pcBtn = document.querySelector('.buybtn, .buy-btn, .buy-button, [class*="buyBtn"]');
    if (pcBtn) { pcBtn.click(); return 'clicked: ' + pcBtn.textContent.trim(); }
    return 'not_found';
})()
"""

JS_SELECT_SESSION = """
(index) => {
    // 选择场次（第index个）
    const items = document.querySelectorAll('[class*="perform"] [class*="item"], [class*="session"] [class*="item"], [class*="sku"] li');
    if (items.length > index) {
        items[index].click();
        return 'selected session ' + index + ': ' + items[index].textContent.trim().substring(0, 30);
    }
    return 'no session items found (count=' + items.length + ')';
}
"""

JS_SELECT_TICKET_TIER = """
(index) => {
    // 选择票档（第index个）
    const items = document.querySelectorAll('[class*="price"] [class*="item"], [class*="grade"] [class*="item"]');
    if (items.length > index) {
        items[index].click();
        return 'selected tier ' + index + ': ' + items[index].textContent.trim().substring(0, 30);
    }
    return 'no tier items found (count=' + items.length + ')';
}
"""

JS_SET_TICKET_COUNT = """
(count) => {
    // 设置购票数量
    const plus = document.querySelector('[class*="num"] [class*="plus"], [class*="count"] [class*="add"], .plus-btn');
    if (!plus) return 'plus button not found';
    // 先看当前数量
    const numEl = document.querySelector('[class*="num"] input, [class*="count"] input, [class*="num"] span[class*="val"]');
    let current = numEl ? parseInt(numEl.value || numEl.textContent) : 1;
    if (isNaN(current)) current = 1;
    const clicks = count - current;
    for (let i = 0; i < clicks; i++) plus.click();
    return 'set count to ' + count + ' (clicked plus ' + clicks + ' times)';
}
"""

JS_SELECT_VIEWERS = """
(names) => {
    // 选择观演人
    // 先点击"选择观演人"入口
    const entry = Array.from(document.querySelectorAll('div, span, a')).find(
        el => el.textContent.includes('选择观演人') || el.textContent.includes('添加观演人')
    );
    if (entry) entry.click();

    let selected = 0;
    setTimeout(() => {
        const checkboxes = document.querySelectorAll('[class*="viewer"] [class*="item"], [class*="contact"] [class*="item"]');
        for (const cb of checkboxes) {
            const name = cb.textContent.trim();
            if (names.length === 0 || names.some(n => name.includes(n))) {
                cb.click();
                selected++;
            }
        }
        // 确认
        const confirm = Array.from(document.querySelectorAll('button, div[class*="confirm"], a')).find(
            el => el.textContent.includes('确认') || el.textContent.includes('确定')
        );
        if (confirm) confirm.click();
    }, 300);
    return 'selecting viewers, count=' + names.length;
}
"""

JS_SUBMIT_ORDER = """
(() => {
    const btns = document.querySelectorAll('button, div[class*="submit"], div[class*="confirm"], a[class*="submit"]');
    for (const btn of btns) {
        const text = btn.textContent.trim();
        if (text.includes('提交订单') || text.includes('确认订单') || text.includes('立即支付')) {
            btn.click();
            return 'submitted: ' + text;
        }
    }
    return 'submit button not found';
})()
"""

JS_GET_PAGE_INFO = """
(() => {
    return {
        url: location.href,
        title: document.title,
        readyState: document.readyState
    };
})()
"""

JS_DETECT_APP_ONLY_CHANNEL = """
(() => {
    const text = document.body ? (document.body.innerText || '') : '';
    const needles = [
        '该渠道不支持购票', '请到大麦App购买', '请到大麦App', '请到APP购票',
        '请前往APP', '请使用大麦APP', '请前往APP购买'
    ];
    for (const n of needles) {
        if (text.includes(n)) return { blocked: true, matched: n };
    }
    return { blocked: false };
})()
"""


def normalize_target_url_for_mobile(target_url):
    """将 PC 详情 item.htm?id= 转为 H5 m.damai.cn/shows/item.html?itemId=。"""
    if not target_url or "YOUR_ITEM_ID" in target_url or "ITEM_ID" in target_url:
        return target_url
    try:
        u = urlparse(target_url.strip())
        host = (u.netloc or "").lower()
        if "detail.damai.cn" in host and "item.htm" in u.path:
            q = parse_qs(u.query)
            ids = q.get("id") or q.get("itemId")
            if ids and ids[0].isdigit():
                return f"https://m.damai.cn/shows/item.html?itemId={ids[0]}"
    except Exception:
        pass
    return target_url


async def apply_mobile_emulation(cdp, config):
    """CDP 模拟手机视口与 UA，便于打开 m.damai.cn H5。"""
    ua = (config.get("mobile_user_agent") or "").strip() or DEFAULT_MOBILE_UA
    vw = int(config.get("mobile_viewport_width", 390))
    vh = int(config.get("mobile_viewport_height", 844))
    dpr = float(config.get("mobile_device_scale_factor", 3))

    await cdp.send("Network.enable")
    await cdp.send(
        "Network.setUserAgentOverride",
        {"userAgent": ua, "acceptLanguage": "zh-CN,zh;q=0.9", "platform": "iPhone"},
    )
    await cdp.send(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": vw,
            "height": vh,
            "deviceScaleFactor": dpr,
            "mobile": True,
        },
    )
    await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})


async def check_app_only_and_exit(cdp):
    """若页面明确仅 App 渠道，退出并提示。"""
    try:
        r = await cdp.evaluate(JS_DETECT_APP_ONLY_CHANNEL)
    except Exception:
        return False
    if not r or not r.get("blocked"):
        return False
    log(
        "检测到「仅大麦 App 购票」类提示（匹配: %s）。浏览器/CDP 无法替代原生 App，请改用大麦 App 或选择仍开放网页购票的场次。"
        % r.get("matched", "?"),
        "ERROR",
    )
    return True


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{level}] {msg}")


async def wait_until(target_time_str):
    """等待到指定时间"""
    target = datetime.strptime(target_time_str, "%Y-%m-%d %H:%M:%S")
    while True:
        now = datetime.now()
        diff = (target - now).total_seconds()
        if diff <= 0:
            log("到达目标时间！开始抢票！", "START")
            return
        if diff > 60:
            log(f"距离开抢还有 {diff:.0f} 秒 ({diff/60:.1f} 分钟)，等待中...")
            await asyncio.sleep(min(diff - 30, 30))
        elif diff > 5:
            log(f"距离开抢还有 {diff:.1f} 秒，准备就绪...")
            await asyncio.sleep(1)
        else:
            # 最后几秒，高精度等待
            log(f"倒计时 {diff:.2f} 秒...")
            await asyncio.sleep(0.05)


async def refresh_page(cdp):
    """刷新页面"""
    await cdp.send("Page.reload")
    await asyncio.sleep(1)


async def run(config):
    host = config["cdp_host"]
    port = config["cdp_port"]
    mobile_mode = bool(config.get("mobile_mode"))
    nav_url = config["target_url"]
    if mobile_mode:
        nav_url = normalize_target_url_for_mobile(nav_url)
        if nav_url != config["target_url"]:
            log(f"mobile_mode: 已切换为 H5 地址 {nav_url[:100]}")
    target_time = config.get("target_time")
    session_idx = config.get("session_index", 0)
    tier_idx = config.get("ticket_tier_index", 0)
    ticket_count = config.get("ticket_count", 1)
    viewer_names = config.get("viewer_names", [])
    poll_ms = config.get("poll_interval_ms", 200)
    retry_count = config.get("retry_count", 50)
    delay_range = config.get("random_delay_ms", [50, 150])

    # ── 1. 连接 CDP ──
    log("正在连接 Chrome CDP...")
    try:
        ws_url = get_ws_url(host, port, nav_url)
    except Exception as e:
        sys.exit(f"无法连接 Chrome CDP ({host}:{port}): {e}\n"
                 f"请确认:\n"
                 f"  1. Mac Chrome 已启动 --remote-debugging-port=9222\n"
                 f"  2. SSH 转发已建立: ssh -L 9222:localhost:9222 caihaolun@192.168.0.10")

    log(f"WebSocket: {ws_url}")
    cdp = CDPClient(ws_url)
    await cdp.connect()
    log("CDP 连接成功！")

    # 启用需要的域
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")

    if mobile_mode:
        log("mobile_mode: 启用移动 UA / 视口 / 触摸仿真")
        await apply_mobile_emulation(cdp, config)

    # 获取当前页面信息
    info = await cdp.evaluate(JS_GET_PAGE_INFO)
    log(f"当前页面: {info.get('title', 'N/A')} - {info.get('url', 'N/A')[:80]}")

    # ── 2. 导航到目标页面 ──
    current_url = info.get("url", "")
    placeholder = "YOUR_ITEM_ID" in nav_url or "ITEM_ID" in nav_url
    if nav_url and not placeholder and nav_url not in current_url:
        log(f"导航到目标页面: {nav_url[:80]}")
        await cdp.send("Page.navigate", {"url": nav_url})
        await asyncio.sleep(3)
        info = await cdp.evaluate(JS_GET_PAGE_INFO)
        log(f"已到达: {info.get('title', 'N/A')}")
    elif placeholder:
        log("target_url 含占位符，跳过自动导航（请在已打开标签进入目标场次）", "WARN")
    else:
        await asyncio.sleep(0.3)

    if await check_app_only_and_exit(cdp):
        await cdp.close()
        sys.exit(2)

    # ── 3. 预选场次和票档 ──
    log("尝试预选场次和票档...")
    result = await cdp.evaluate(f"({JS_SELECT_SESSION})({session_idx})")
    log(f"  场次: {result}")
    await asyncio.sleep(0.5)

    result = await cdp.evaluate(f"({JS_SELECT_TICKET_TIER})({tier_idx})")
    log(f"  票档: {result}")
    await asyncio.sleep(0.3)

    if ticket_count > 1:
        result = await cdp.evaluate(f"({JS_SET_TICKET_COUNT})({ticket_count})")
        log(f"  数量: {result}")

    # ── 4. 等待开抢时间 ──
    if target_time:
        await wait_until(target_time)
        # 开抢前刷新页面
        log("刷新页面...")
        await refresh_page(cdp)
        if await check_app_only_and_exit(cdp):
            await cdp.close()
            sys.exit(2)
        # 重新选择场次票档
        await cdp.evaluate(f"({JS_SELECT_SESSION})({session_idx})")
        await asyncio.sleep(0.3)
        await cdp.evaluate(f"({JS_SELECT_TICKET_TIER})({tier_idx})")
        await asyncio.sleep(0.2)
        if ticket_count > 1:
            await cdp.evaluate(f"({JS_SET_TICKET_COUNT})({ticket_count})")

    # ── 5. 抢票循环 ──
    log(f"开始抢票循环（最多 {retry_count} 轮）...")
    for attempt in range(1, retry_count + 1):
        # 检测购买按钮状态
        btn_status = await cdp.evaluate(JS_CHECK_BUY_BUTTON)

        if not btn_status or not btn_status.get("found"):
            log(f"  [{attempt}] 未找到购买按钮，刷新页面...")
            await refresh_page(cdp)
            await cdp.evaluate(f"({JS_SELECT_SESSION})({session_idx})")
            await asyncio.sleep(0.3)
            await cdp.evaluate(f"({JS_SELECT_TICKET_TIER})({tier_idx})")
            continue

        if not btn_status.get("clickable"):
            status_text = btn_status.get("text", "unknown")
            if attempt % 10 == 1:
                log(f"  [{attempt}] 按钮状态: {status_text}，等待...")
            # 随机延迟后刷新
            delay = random.randint(delay_range[0], delay_range[1]) / 1000
            await asyncio.sleep(delay)
            await refresh_page(cdp)
            await cdp.evaluate(f"({JS_SELECT_SESSION})({session_idx})")
            await asyncio.sleep(0.2)
            await cdp.evaluate(f"({JS_SELECT_TICKET_TIER})({tier_idx})")
            continue

        # ── 按钮可点击！立即抢！──
        log(f"  [{attempt}] 按钮可点击: {btn_status.get('text')}！点击中...", "GO")
        click_result = await cdp.evaluate(JS_CLICK_BUY_BUTTON)
        log(f"  点击结果: {click_result}")

        # 等待页面跳转到订单确认页
        await asyncio.sleep(1.5)
        info = await cdp.evaluate(JS_GET_PAGE_INFO)
        log(f"  当前页面: {info.get('url', 'N/A')[:80]}")

        # 如果跳转到了确认订单页面
        page_url = info.get("url", "")
        if "order" in page_url or "confirm" in page_url or "buy" in page_url:
            log("进入订单确认页！", "OK")

            # 选择观演人
            if viewer_names:
                log(f"选择观演人: {viewer_names}")
                await cdp.evaluate(f"({JS_SELECT_VIEWERS})({json.dumps(viewer_names)})")
                await asyncio.sleep(1)

            # 提交订单
            await asyncio.sleep(0.5)
            submit_result = await cdp.evaluate(JS_SUBMIT_ORDER)
            log(f"提交订单: {submit_result}", "OK")

            # 检查是否进入支付页
            await asyncio.sleep(2)
            info = await cdp.evaluate(JS_GET_PAGE_INFO)
            log(f"当前页面: {info.get('title', 'N/A')} - {info.get('url', 'N/A')[:80]}")

            if "pay" in info.get("url", "").lower() or "cashier" in info.get("url", "").lower():
                log("已进入支付页面！请手动完成支付！", "SUCCESS")
                # 发出声音提示（如果可能）
                await cdp.evaluate("new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQ==').play().catch(()=>{})")
                break
            else:
                log("订单已提交，请检查页面状态", "OK")
                break
        else:
            # 可能需要再次尝试（库存瞬间没了等）
            log(f"  点击后未跳转到订单页，继续尝试...")
            delay = random.randint(delay_range[0], delay_range[1]) / 1000
            await asyncio.sleep(delay)

    else:
        log(f"已达到最大重试次数 ({retry_count})，停止", "WARN")

    log("脚本结束")
    await cdp.close()


async def test_connection(config):
    """测试 CDP 连接"""
    host = config["cdp_host"]
    port = config["cdp_port"]

    log("测试 CDP 连接...")
    try:
        url = f"http://{host}:{port}/json/version"
        with urllib.request.urlopen(url, timeout=5) as resp:
            version_info = json.loads(resp.read())
        log(f"Chrome 版本: {version_info.get('Browser', 'unknown')}")
        log(f"协议版本: {version_info.get('Protocol-Version', 'unknown')}")
    except Exception as e:
        sys.exit(f"连接失败: {e}")

    try:
        ws_url = get_ws_url(host, port)
        log(f"WebSocket URL: {ws_url}")
        cdp = CDPClient(ws_url)
        await cdp.connect()
        info = await cdp.evaluate(JS_GET_PAGE_INFO)
        log(f"当前页面: {info.get('title', 'N/A')}")
        log(f"URL: {info.get('url', 'N/A')}")
        await cdp.close()
        log("连接测试通过！", "OK")
    except Exception as e:
        sys.exit(f"WebSocket 连接失败: {e}")


def main():
    config = load_config()

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(test_connection(config))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        # 列出所有打开的页面
        host = config["cdp_host"]
        port = config["cdp_port"]
        url = f"http://{host}:{port}/json"
        with urllib.request.urlopen(url, timeout=5) as resp:
            pages = json.loads(resp.read())
        print(f"\n共 {len(pages)} 个标签页:\n")
        for i, page in enumerate(pages):
            print(f"  [{i}] {page.get('title', 'N/A')[:50]}")
            print(f"      {page.get('url', 'N/A')[:80]}")
            print()
        return

    print("=" * 60)
    print("  大麦网自动抢票脚本")
    print("=" * 60)
    if config.get("mobile_mode"):
        nu = normalize_target_url_for_mobile(config["target_url"])
        print(f"  mobile_mode: 开（H5: {nu[:65]}…）" if len(nu) > 65 else f"  mobile_mode: 开（H5: {nu}）")
    print(f"  目标: {config['target_url'][:60]}")
    if config.get("target_time"):
        print(f"  开抢时间: {config['target_time']}")
    print(f"  场次: #{config.get('session_index', 0)}")
    print(f"  票档: #{config.get('ticket_tier_index', 0)}")
    print(f"  数量: {config.get('ticket_count', 1)}")
    print(f"  轮询间隔: {config.get('poll_interval_ms', 200)}ms")
    print("=" * 60)
    print()

    asyncio.run(run(config))


if __name__ == "__main__":
    main()
