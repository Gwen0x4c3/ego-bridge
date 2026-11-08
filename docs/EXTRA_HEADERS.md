# Extra headers：页面级验证结果

在 ego-browser **0.6.2.9** 上实测（2026-11-09）。当前 SDK 的 `taskSpace()` 没有
额外请求头配置参数，`page.goto()` 也没有通用 `headers` 参数，但可以使用公开的
`page.cdp()` 调用 [Network.setExtraHTTPHeaders](https://chromedevtools.github.io/devtools-protocol/tot/Network/#method-setExtraHTTPHeaders)。

## 已验证的范围

使用本机回显 HTTP 服务、独立 Agent 验证空间测试，没有修改已有用户页面或浏览器配置。

| 场景 | 结果 |
| --- | --- |
| 设置后的同轮页面导航 | 请求头到达服务端 |
| 同轮页面刷新 | 请求头到达服务端 |
| 同轮页面 `fetch()` | 请求头到达服务端 |
| 同空间新建另一个页面 | 不继承 |
| 下一轮 CLI 调用恢复同一个页面 | 不保留 |
| 下一轮重新设置，再导航 | 生效 |
| 传入空对象清除 | 后续请求不携带 |

因此这是一种 **单页面、单次 CLI 调用内的能力**。每次脚本运行、每个目标页面都需要
重新设置；不能把它视为 workspace 的持久环境设置。跨域 iframe、Service Worker、
弹出窗口和跨域重定向尚未验证。

## 通用用法

```bash
ego-browser nodejs <<'JS'
const task = await taskSpace("extra headers example");
const page = task.page("p1");
await page.cdp("Network.enable");
await page.cdp("Network.setExtraHTTPHeaders", {
  headers: {
    "X-Environment": "preview",
    "X-Preview-Route": "feature-demo"
  }
});
try {
  // 替换成支持这些路由请求头的应用地址。
  await page.goto("https://example.com");
  console.log(await page.title());
} finally {
  await page.cdp("Network.setExtraHTTPHeaders", {headers: {}});
}
await task.finish({keep: []});
JS
```

Header 值是字符串。设置会影响该页面在当前 CDP 会话中的后续请求，不能按请求 URL
自动限定作用范围。运行另一个页面前要对那个页面单独配置。

暂未加入 popup 的全局开关，以免把短暂的页面设置误呈现为整个 workspace 的持久配置。
如需后续产品化，应由 Agent 在每个执行轮次、每个新页面操作前显式应用配置，并增加
按 origin 限定、清除和继承的测试。

## 复现

先使用 `taskSpace()` 创建自己控制的验证空间，记下它的 `spaceId`，然后：

```bash
python3 tests/explore_extra_headers.py --space <spaceId> --output /tmp/extra-headers-results.json
```

测试只向 `127.0.0.1` 回显服务发送 `X-Bridge-Environment: preview-demo`。
结束后，调用 `task.finish({keep: []})` 清理该验证空间。
