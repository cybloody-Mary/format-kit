# QQ 音乐转码平台

在 macOS 和 Windows 上整理 QQ 音乐的本地下载，把支持的歌曲恢复成其他播放器可播放的 **FLAC 或 OGG**，也可以额外导出 **MP3**。应用会刷新下载列表，记录转换结果，并识别已经处理过的歌曲。

[下载源码 ZIP](https://github.com/cybloody-Mary/qqmusic-decoder/archive/refs/heads/main.zip) · [安装与构建](#安装与构建) · [选哪个音质模式](#选哪个音质模式) · [许可证](#许可证)

## 快速使用

已有便携应用包可以直接按下面操作。刚下载源码 ZIP 的话，请先[构建应用包](#安装与构建)。

1. 解压整个应用包。macOS 打开 `QQ音乐转码平台.app`。Windows 双击带紫色图标的 `Start.exe`，应用会在默认浏览器中打开。
2. 首次启动时选择 **下载目录** 和 **导出目录**。下载目录应直接包含歌曲文件。应用会尝试识别常见路径。之后可在 macOS 的「目录设置」或 Windows 的「工作目录」中更改。默认导出到系统音乐目录下的 `QQ音乐转换`。
3. 在「待处理」列表中勾选歌曲。可以按歌名或歌手搜索，按音质、源格式或歌手筛选和分组，或按歌名首字母排序。
4. 选择转换模式，开始并确认本批次。转换完成后，从导出目录的 `原音` 或 `MP3` 子文件夹打开歌曲。

应用打开期间会定期刷新列表，Windows 请保持浏览器页面可见。新下载的歌曲进入待处理列表，开始转换仍需手动确认。导出目录中的 `.qqmusic-ledger.json` 记录转换状态，`QQ音乐转码清单.txt` 便于查看结果。继续使用同一个导出目录即可沿用台账。失败的歌曲可以检查原因后重新勾选。

## 选哪个音质模式

| 界面模式 | 输出 | 适合 |
| --- | --- | --- |
| QMC Decoder · 仅原音 | FLAC 或 OGG | 优先保留音质 |
| TuneFree · 320 kbps + 原音 | FLAC 或 OGG，另加一份 320 kbps MP3 | 需要固定码率的 MP3 |
| QM Unlock · V2 + 原音 | FLAC 或 OGG，另加一份 LAME V2 可变码率 MP3 | 需要体积通常更小的 MP3 |

如果原音是无损 FLAC，「仅原音」会保留无损音频。源音频为有损 OGG 时，会保留原有编码和音质。两种 MP3 预设都是有损编码，附带的原音仍会保留。

## 支持的文件与密钥

| 输入 | 条件 |
| --- | --- |
| 普通 `.flac`、`.ogg` | 直接从本地处理 |
| `.mflac`、`.mgg` 等 QQ 音乐下载文件 | 支持内嵌密钥的版本。新版 `musicex` 需要对应歌曲的密钥 |
| `.qmcflac`、`.qmcogg`、`.qmc2` | 可尝试恢复支持的旧版文件 |

macOS 上，你可以逐批确认使用本机 QQ 音乐的登录状态。确认后，应用读取客户端偏好设置，仅通过腾讯的 QQ 音乐 HTTPS 接口请求本批所选歌曲的密钥。凭据仅在内存中使用，音频在本机处理。Windows 上遇到新版 `musicex` 文件时，需要提供对应的单曲密钥。请仅处理你有权转换的音频。

## 安装与构建

GitHub 当前提供源码和构建脚本。上方的「下载源码 ZIP」是源码包。按下面的步骤生成便携应用包，再解压运行。依赖下载脚本会通过 SHA-256 校验固定版本的依赖，生成的应用自带 Python 和 FFmpeg。

先下载并解压源码，或克隆仓库。以下各平台命令均在包含 `source` 的仓库目录中执行。

```sh
git clone https://github.com/cybloody-Mary/qqmusic-decoder.git
cd qqmusic-decoder
```

### macOS 14 或更新版本

构建需要 Python 3.9 或更新版本，以及支持 Swift 6 的 Xcode 或 Xcode Command Line Tools。

```sh
python3 source/fetch_dependencies.py macos
python3 source/package_portable.py macos
```

产物 `dist/QQMusicStudio-2.0.1-macOS-universal.zip` 同时支持 Apple 芯片和 Intel 芯片。解压后打开 `QQ音乐转码平台.app`。打包默认使用临时签名。需要向其他人分发时，可使用 Developer ID 签名并提交 Apple 公证。

如果有多个兼容的 macOS SDK，可以显式指定已安装的 SDK。例如，使用已验证过的 macOS 26.5 SDK。

```sh
python3 source/package_portable.py macos --sdk "$(xcrun --sdk macosx26.5 --show-sdk-path)"
```

### Windows x64 或 ARM64

构建需要 Python 3.9 或更新版本，以及用于编译启动器的 Zig（已验证 0.15.2）。将 Zig 加入 `PATH`，或向打包命令传入 `--zig` 和 Zig 可执行文件的完整路径。便携包自带运行时。在 PowerShell 中执行以下命令。若 `py` 命令不可用，可用 `python` 代替 `py -3`。

```powershell
py -3 source/fetch_dependencies.py windows-x64
py -3 source/package_portable.py windows-x64
```

ARM64 电脑将两条命令中的 `windows-x64` 改成 `windows-arm64`。在 `dist/` 找到对应的 `QQMusicStudio-2.0.1-Windows-x64.zip` 或 `QQMusicStudio-2.0.1-Windows-arm64.zip`，解压整个文件夹后双击 `Start.exe`。若打开失败，可用同目录的 `Start.cmd` 查看错误信息。网页服务只在本机运行，可点浏览器界面的「退出」关闭应用。

## 常见问题

- **应用找不到歌曲怎么办？** 在 QQ 音乐客户端查看下载位置，选择实际包含歌曲文件的那一级目录。macOS 可在 Finder 中按 `⌘⇧G` 输入 `~/Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac/iQmc`。
- **下载中的歌曲什么时候能处理？** 等 QQ 音乐完成下载后再刷新。应用识别到完整的支持格式后，会将歌曲列为待转码或显示其已有状态。
- **只导出了原音，之后还能补 MP3 吗？** 可以。在同一个导出目录里找到该歌曲，选任一带 MP3 的模式。已通过核验的原音可以直接用于编码。
- **换了导出目录，为什么看不到旧记录？** 台账跟随导出目录。切回原来的目录即可查看之前的转换状态。

## 测试

macOS 后端集成测试使用合成音频。将 `QQMUSIC_TEST_FFMPEG` 指向带 `libmp3lame` 的 FFmpeg 可执行文件后运行。

```sh
PYTHONPATH=source QQMUSIC_TEST_FFMPEG="$(command -v ffmpeg)" python3 -B -m unittest discover -s tests
```

打包后可运行 `tests/test_portable_flow.py`。将包含 `studio.py` 和 FFmpeg 的资源目录作为参数，macOS 使用 `.app` 内的 `Contents/Resources`，Windows 使用解压后的 `QQMusicStudio` 目录。

## 许可证

项目原创部分采用 [PolyForm Noncommercial 1.0.0](LICENSE)，仅授权符合条款的非商用使用、修改和分发。分发时请按许可保留许可证文本与 [NOTICE](NOTICE)。TuneFree for Mac 上游代码保留 [MIT 许可](source/tunefree_backend/LICENSE)，Python、FFmpeg 等组件遵循 [各自的许可](source/Notices/)。本项目是独立工具，与腾讯或 QQ 音乐无隶属关系。
