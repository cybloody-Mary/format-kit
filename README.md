# QQ 音乐转码平台

一个在 macOS 和 Windows 上运行的本地音乐整理工具。选择下载目录和导出目录后，可以查看待转码、成功、失败和下载未完成的歌曲，按歌名或歌手搜索，按音质、源格式和歌手筛选或分组，并按歌名首字母排序。

## 转码与同步

| 模式 | 输出 |
| --- | --- |
| 仅原音 | 保留可播放的 FLAC 或 OGG，沿用源音频编码 |
| 320 kbps + 原音 | 保留原音，另生成一份 LAME 320 kbps MP3 |
| V2 + 原音 | 保留原音，另生成一份 LAME V2 可变码率 MP3 |

优先保留音质时，选「仅原音」。源音频是 FLAC 时可保持无损，源音频是有损 OGG 时保留原有音质。两种 MP3 模式都会额外进行有损编码。

应用打开后会定期扫描下载目录，新歌会加入待处理列表。由你勾选歌曲并确认转码，台账保存在导出目录中，用来识别已处理歌曲。默认导出位置是系统音乐目录下的 `QQ音乐转换`，可在应用里修改。原音与 MP3 分别写入 `原音` 和 `MP3` 子目录。请仅处理你有权转换的音频。

新版 `musicex` 文件在 macOS 上可使用本机 QQ 音乐的登录状态取密钥。应用在你逐批确认后读取 QQ 音乐客户端偏好设置，并仅向腾讯的 QQ 音乐 HTTPS 接口请求所选歌曲的密钥。凭据留在内存中，音频在本机处理。Windows 上使用新版文件时，需要提供对应的单曲密钥。

## 从源码构建

公开仓库提供源码和打包脚本。生成的 ZIP 在 `dist/`，包含运行所需的 Python 和 FFmpeg。下载的依赖会按固定的 SHA-256 校验。

### macOS 14 或更新版本

准备 Python 3.9 或更新版本，以及支持 Swift 6 的 Xcode 或 Xcode Command Line Tools。在 macOS 上运行以下命令，生成同时适用于 Apple 芯片和 Intel 芯片的 `.app` 压缩包。

```sh
python3 source/fetch_dependencies.py macos
python3 source/package_portable.py macos
```

如需使用另一个已安装的 macOS SDK，可向打包命令加上 `--sdk /完整/SDK/路径`。解压后打开 `QQ音乐转码平台.app`。脚本默认使用本地临时签名。通过网络分发 macOS 应用时，需要另外考虑 Developer ID 签名与 Apple 公证。

### Windows x64 或 ARM64

构建时需要 Python 3.9 或更新版本。在 Windows PowerShell 中执行以下命令。ARM64 电脑将两条命令中的 `windows-x64` 改为 `windows-arm64`。

```powershell
py -3 source/fetch_dependencies.py windows-x64
py -3 source/package_portable.py windows-x64
```

解压完整目录后，双击 `Start.cmd`。界面会在默认浏览器中打开，服务仅监听本机。可在界面内退出应用。

## 测试

macOS 后端集成测试使用合成音频。`QQMUSIC_TEST_FFMPEG` 指定带有 `libmp3lame` 的 FFmpeg 可执行文件。

```sh
PYTHONPATH=source QQMUSIC_TEST_FFMPEG="$(command -v ffmpeg)" python3 -B -m unittest discover -s tests
```

`tests/test_portable_flow.py` 可用来测试打包目录，接受包含 `studio.py` 和 FFmpeg 的资源目录路径。

## 许可

项目原创代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)。许可允许非商用的使用、修改和分发，分发时须遵守许可并保留 [NOTICE](NOTICE)。来自 TuneFree for Mac 的代码继续采用 [MIT 许可](source/tunefree_backend/LICENSE)。Python、FFmpeg 等独立组件遵循各自的许可证，见 [第三方组件说明](source/Notices/)。本项目为独立工具，与腾讯或 QQ 音乐无隶属关系。
