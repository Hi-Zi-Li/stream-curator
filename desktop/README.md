# Desktop

这里是 `stream-curator` 的 Electron 客户端外壳。

当前职责很简单：

- 承载 `推送 / 热门 / 搜索 / 设置` 四个页面
- 通过 `preload.js` 调用 Python CLI
- 启动后台 worker，优先读取 SQLite 中已经缓存好的内容
- 打开应用内阅读页
- 在设置页里嵌入三端登录页

## 本地运行

```powershell
cd desktop
npm install
npm start
```

默认情况下，桌面端会去调用项目里的 Python 后端和三个上游 CLI。

如果你的环境路径不同，可以覆盖：

- `STREAM_CURATOR_PYTHON_EXECUTABLE`
- `STREAM_CURATOR_RUNTIME_ROOT`
- `STREAM_CURATOR_BILIBILI_EXECUTABLE`
- `STREAM_CURATOR_ZHIHU_EXECUTABLE`
- `STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE`

## 打包

### 便携目录

```powershell
cd desktop
npm run build:portable
```

输出目录：

- `desktop/dist/stream-curator-win32-x64/`

这个版本继续使用你本机已有的 Python 环境和上游 CLI。

### 自包含发布包

```powershell
cd desktop
npm run build:release
```

输出文件：

- `desktop/dist/stream-curator-release.zip`

这个发布包会带上：

- Electron runtime
- 精简后的 Python runtime
- `stream-curator` 源码
- 三个上游 CLI 包装
- 应用图标和 exe 图标

解压后直接运行 `stream-curator.exe` 即可。
