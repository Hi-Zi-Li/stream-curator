# third-party

这里放 `stream-curator` 依赖的三个上游 CLI fork：

- `@bilibili-cli`
- `@zhihu-cli`
- `@xiaohongshu-cli`

它们以 git submodule 的方式挂进来，目的很直接：

- 不再要求每次新机器都手动 clone 三个仓库
- 让 `stream-curator` 仓库本身就携带完整的上游源码引用
- 开发态默认优先走 `third-party/bin/*.cmd` 包装，而不是你机器上的固定绝对路径

## 克隆

首次克隆时请带上 submodule：

```powershell
git clone --recurse-submodules <your-stream-curator-repo>
```

如果已经 clone 过主仓库，再补：

```powershell
git submodule update --init --recursive
```

## 包装命令

`third-party/bin/` 下有三个本地包装：

- `bili.cmd`
- `zhihu.cmd`
- `xhs.cmd`

它们会优先使用 `STREAM_CURATOR_PYTHON_EXECUTABLE`，并把对应 submodule 根目录加到 `PYTHONPATH`，这样 `stream-curator` 可以直接调起仓库内的上游源码。
