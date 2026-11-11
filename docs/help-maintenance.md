# 帮助指南维护

帮助页使用 `#help` 路由，章节直达形式为 `#help?section=quickstart`，搜索使用 `q` 参数。

## 内容和导出

- 文案：`frontend/src/help/content.json`。
- 页面与搜索：`frontend/src/Help.tsx`、`frontend/src/help/model.ts`。
- 样式：`frontend/src/help/help.css`。
- 图片：`frontend/public/help/images/`。
- 导出：`frontend/scripts/build-help.mjs`。

在frontend目录执行 `npm run help:export` 或 `npm run build`，生成 `public/help/apex-guide.html` 和 `.md`。导出文件内嵌图片，可离线阅读；生成文件不手动维护。

## 更新截图

`capture-help.py`、`help_browser.py` 提供隔离演示接口，`verify-help.py` 用于页面和下载验证。截图标注为演示数据，不代表真实政策、账单或正式评测成绩。脚本所需浏览器依赖需另行准备。

维护时检查章节链接、搜索、截图加载、图片放大、导出文件和窄屏布局，并运行前端类型检查、测试及生产构建。
