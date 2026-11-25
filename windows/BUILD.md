# 构建 Windows x64 版

需要 Python 3.12 或更新版本、Pillow、Go 1.26.6 和可生成 Windows x64 对象文件的 Clang。支持从已配置这些工具的 macOS 或 Windows 构建。在仓库根目录执行以下命令。

```sh
python3 -m pip install Pillow
python3 source/fetch_dependencies.py windows-x64
python3 source/package_portable.py windows-x64
```

产物为 `dist/FormatKit-Windows-x64.zip`。打包程序核验 CPython、FFmpeg 和 Kugo 源码的 SHA-256，随后构建启动器和转换引擎。Go 按锁定版本下载依赖。保留这些依赖的本地缓存即可离线重建。可用 `--go` 和 `--clang` 指定工具路径。

引擎的上游源码由下载脚本固定到 Kugo v0.6.1。原始工程由 `engine/main.go` 提供命令入口，由 `engine/protocol_output.go` 隔离结构化输出和日志。打包时会将两份文件及上游源码一并放入 `source/kugo-engine`。请参阅[第三方组件](licenses/第三方组件.md)了解许可。新 Windows 应用通过这里的 Python 服务和网页调用该引擎。

## 重建已解压包里的引擎和启动器

已发布的便携包内附所用源码。以下命令使用 Bash，先进入解压后的 `FormatKit` 目录。

```sh
cd source/kugo-engine/backend
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -buildvcs=false \
  -ldflags="-s -w" -o ../../../engine/format-kit-engine.exe \
  ./cmd/format-kit-engine
cd ../../launcher
python3 icon_resource.py ../../web/cover.png icon_windows_amd64.syso
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -buildvcs=false \
  -ldflags="-s -w -H=windowsgui" -o "../../Start Format Kit.exe" .
```

开发时可运行 `server.py --engine <引擎路径> --ffmpeg <FFmpeg路径> --no-browser` 检查本地网页和批次管理。
