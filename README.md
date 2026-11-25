# Format Kit

<img src="source/web/app-icon.png" alt="紫色圆角播放键与声波应用图标" width="104" height="104" align="right">

在 macOS 和 Windows 上整理 QQ 音乐的本地下载，把支持的歌曲恢复成其他播放器可播放的 **FLAC 或 OGG**，也可以额外导出 **MP3**。应用会刷新下载列表，记录转换结果，并识别已经处理过的歌曲。

## 下载最新版

**[打开官方下载站](https://nuomii.top/tools/format-kit/download/)**，也可在 [GitHub 发布页](https://github.com/cybloody-Mary/format-kit/releases/latest) 下载对应系统的便携包。

| 系统 | 下载 | 解压后打开 |
| --- | --- | --- |
| macOS 14+（Apple 芯片和 Intel） | [macOS 通用版 ZIP](https://github.com/cybloody-Mary/format-kit/releases/latest/download/FormatKit-macOS-universal.zip) | `Format Kit.app` |
| Windows 10 或 11 x64（Intel / AMD） | [Windows x64 ZIP](https://github.com/cybloody-Mary/format-kit/releases/latest/download/FormatKit-Windows-x64.zip) | `Start Format Kit.exe` |

Windows 包自带转换引擎和 FFmpeg。macOS 公开发行包会在首次转换时从 PyPI 下载并校验固定版本的 FFmpeg。两套应用的音频均在本机处理。

GitHub 的「Code → Download ZIP」和发布页中的「Source code」提供[源码](https://github.com/cybloody-Mary/format-kit/archive/refs/heads/main.zip)。

## 快速使用

### Windows x64

1. 解压整个应用包，双击 `Start Format Kit.exe`。应用会在默认浏览器中打开本地页面。
2. 点击「目录设置」，选择 QQ 音乐的下载目录和导出目录。
3. 勾选歌曲，选择 **保持原格式**或 **MP3**，点击「开始转换」。当前进度和结果均可在页面查看。退出时点击侧栏「退出」。

保持原格式会保留源音频编码。FLAC 音源仍为 FLAC，OGG 音源仍为 OGG。MP3 转码提供 V0、V2、V5、V7 档位。歌曲可以按歌名或歌手搜索，按音质、格式和歌手筛选或分组。同名输出会增加序号。

Windows 版集成 Kugo v0.6.1 转换引擎，支持 `.mflac`、`.mgg` 和若干旧式 QMC 格式。处理新版 `musicex` 歌曲时，需要有效的 QQ 音乐客户端登录、对应歌曲权限和网络连接。转换历史可查看每首歌曲的结果。[Windows 使用说明](windows/使用说明.md)

### macOS

1. 解压整个应用包，打开 `Format Kit.app`。
2. 首次启动时选择下载目录和导出目录。下载目录应直接包含歌曲文件。需要更改时，使用「目录设置」。
3. 在「待处理」列表中勾选歌曲，选择转换模式并确认本批次。转换完成后，从导出目录的 `原音` 或 `MP3` 子文件夹打开歌曲。

macOS 版支持普通 FLAC、OGG、含内嵌密钥的 `.mflac` 和 `.mgg`，以及部分旧式 QMC 文件。用户可逐批授权使用 QQ 音乐客户端登录状态，为选中的新版 `musicex` 文件取得密钥。

| 界面模式 | 输出 |
| --- | --- |
| QMC Decoder · 仅原音 | FLAC 或 OGG |
| TuneFree · 320 kbps + 原音 | FLAC 或 OGG，另加一份 320 kbps MP3 |
| QM Unlock · V2 + 原音 | FLAC 或 OGG，另加一份 LAME V2 可变码率 MP3 |

macOS 版在导出目录中保存 `.format-kit-ledger.json` 和 `Format Kit 转码清单.txt`。台账随导出目录保存。请仅处理你有权转换的音频。

## 安装与构建

依赖下载脚本会通过 SHA-256 校验固定版本的依赖。macOS 默认构建包含 FFmpeg，使用 `--public` 可构建首次转换时在线准备 FFmpeg 的发行包。

先下载并解压源码，或克隆仓库。以下各平台命令均在包含 `source` 的仓库目录中执行。

```sh
git clone https://github.com/cybloody-Mary/format-kit.git
cd format-kit
```

### macOS 14 或更新版本

构建需要 Python 3.9 或更新版本，以及支持 Swift 6 的 Xcode 或 Xcode Command Line Tools。

```sh
python3 source/fetch_dependencies.py macos
python3 source/package_portable.py macos
```

产物 `dist/FormatKit-macOS-universal.zip` 同时支持 Apple 芯片和 Intel 芯片。解压后打开 `Format Kit.app`。打包默认使用临时签名。需要向其他人分发时，可使用 Developer ID 签名并提交 Apple 公证。

如果有多个兼容的 macOS SDK，可以显式指定已安装的 SDK。例如，使用已验证过的 macOS 26.5 SDK。

```sh
python3 source/package_portable.py macos --sdk "$(xcrun --sdk macosx26.5 --show-sdk-path)"
```

### Windows x64

Windows 版需要 Python 3.12 或更新版本、Pillow、Go 1.26.6 和 Clang。构建输出为 `dist/FormatKit-Windows-x64.zip`。

```sh
python3 -m pip install Pillow
python3 source/fetch_dependencies.py windows-x64
python3 source/package_portable.py windows-x64
```

[Windows 构建和源码说明](windows/BUILD.md)

## 常见问题

- **macOS 提示无法验证开发者？** GitHub 便携包使用临时签名。确认从本项目发布页下载后，可参考 Apple 关于打开未公证应用的说明。[Apple 支持](https://support.apple.com/zh-cn/102445)
- **应用找不到歌曲怎么办？** 在 QQ 音乐客户端查看下载位置，再选择实际的下载目录。macOS 可在 Finder 中按 `⌘⇧G` 输入 `~/Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac/iQmc`。
- **下载中的歌曲什么时候能处理？** 等 QQ 音乐完成下载后再刷新。应用识别到完整的支持格式后，会将歌曲列为待转码或显示其已有状态。
- **只导出了原音，之后还能补 MP3 吗？** 可以，重新选择该歌曲并切换为 MP3。macOS 版可直接使用此前通过核验的原音编码。
- **macOS 换了导出目录后去哪里看旧记录？** 台账跟随导出目录。切回原来的目录即可查看之前的转换状态。

## 测试

macOS 后端集成测试使用合成音频。将 `FORMAT_KIT_TEST_FFMPEG` 指向带 `libmp3lame` 的 FFmpeg 可执行文件后运行。

```sh
PYTHONPATH=source FORMAT_KIT_TEST_FFMPEG="$(command -v ffmpeg)" python3 -B -m unittest discover -s tests
```

macOS 打包后可运行 `tests/test_portable_flow.py`，参数是 `.app` 内包含 `studio.py` 和 FFmpeg 的 `Contents/Resources` 目录。

## 许可证

项目原创部分采用 [PolyForm Noncommercial 1.0.0](LICENSE)，仅授权符合条款的非商用使用、修改和分发。分发时请按许可保留许可证文本与 [NOTICE](NOTICE)。TuneFree for Mac 上游代码保留 [MIT 许可](source/tunefree_backend/LICENSE)。Windows 的 Kugo 转换引擎及其适配代码采用 [GPL-3.0](windows/licenses/KUGO-GPL-3.0.txt)。Python、FFmpeg 等组件遵循各自的许可。[macOS 组件说明](source/Notices/) · [Windows 组件说明](windows/licenses/第三方组件.md)。本项目是独立工具，与腾讯或 QQ 音乐无隶属关系。
