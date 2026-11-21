import AppKit
import Foundation
import SwiftUI

private enum Status: String, Codable, CaseIterable {
    case pending
    case success
    case failed
    case repair
    case incomplete
    case originalReady = "original_ready"

    var label: String {
        switch self {
        case .pending: "待转码"
        case .success: "已成功"
        case .failed: "失败"
        case .repair: "待补齐"
        case .incomplete: "下载未完成"
        case .originalReady: "待补 MP3"
        }
    }

    var icon: String {
        switch self {
        case .pending: "clock"
        case .success: "checkmark.circle"
        case .failed: "exclamationmark.circle"
        case .repair: "wrench.adjustable"
        case .incomplete: "arrow.down.to.line"
        case .originalReady: "music.note"
        }
    }

    var color: Color {
        switch self {
        case .pending, .originalReady: .accentColor
        case .success: .green
        case .failed: .red
        case .repair: .orange
        case .incomplete: .secondary
        }
    }
}

private enum Preset: String, CaseIterable, Identifiable {
    case tunefree
    case qm
    case qmc

    var id: String { rawValue }

    var label: String {
        switch self {
        case .tunefree: "TuneFree · 320 kbps + 原音"
        case .qm: "QM Unlock · V2 + 原音"
        case .qmc: "QMC Decoder · 仅原音"
        }
    }
}

private enum Filter: String, CaseIterable, Identifiable {
    case actionable
    case all
    case success
    case failed
    case incomplete

    var id: String { rawValue }

    var label: String {
        switch self {
        case .actionable: "待处理"
        case .all: "全部歌曲"
        case .success: "已成功"
        case .failed: "失败"
        case .incomplete: "下载未完成"
        }
    }

    var icon: String {
        switch self {
        case .actionable: "tray"
        case .all: "music.note.list"
        case .success: "checkmark.circle"
        case .failed: "exclamationmark.circle"
        case .incomplete: "arrow.down.to.line"
        }
    }

    func matches(_ status: Status) -> Bool {
        switch self {
        case .actionable: status == .pending || status == .failed || status == .repair || status == .originalReady
        case .all: true
        case .success: status == .success
        case .failed: status == .failed
        case .incomplete: status == .incomplete
        }
    }

    var emptyLabel: String {
        switch self {
        case .actionable: "没有待处理的歌曲"
        case .all: "没有找到歌曲"
        case .success: "暂无成功记录"
        case .failed: "暂无失败记录"
        case .incomplete: "没有下载未完成的歌曲"
        }
    }
}

private enum SearchScope: String, CaseIterable, Identifiable {
    case all = "全部"
    case title = "歌名"
    case artists = "歌手"

    var id: String { rawValue }

    var prompt: String {
        switch self {
        case .all: "搜索歌名或歌手"
        case .title: "搜索歌名"
        case .artists: "搜索歌手"
        }
    }
}

private enum Grouping: String, CaseIterable, Identifiable {
    case none = "不分组"
    case quality = "音质"
    case format = "格式"
    case artist = "歌手"

    var id: String { rawValue }
}

private enum TitleSort: String, CaseIterable, Identifiable {
    case ascending = "A → Z"
    case descending = "Z → A"

    var id: String { rawValue }
}

private struct LibraryFacets: Equatable {
    var scope = SearchScope.all
    var quality: String?
    var format: String?
    var artist: String?

    var hasFilters: Bool { quality != nil || format != nil || artist != nil }
}

private struct TrackGroup: Identifiable {
    let id: String
    let title: String?
    let rows: [Track]
}

private let sortLocale = Locale(identifier: "en_US_POSIX")
private let orderedQualities = ["无损", "有损", "未知"]
private let qualityColumnWidth: CGFloat = 138
private let unknownArtist = "未知歌手"

private func alphabeticKey(_ name: String) -> String {
    let latin = name.applyingTransform(.toLatin, reverse: false) ?? name
    let clean = latin.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: sortLocale)
    let beginning = clean.firstIndex(where: { $0.isLetter || $0.isNumber })
    return beginning.map { String(clean[$0...]) } ?? clean
}

private func alphabeticOrder(_ a: String, _ b: String) -> ComparisonResult {
    a.compare(b, options: .numeric, locale: sortLocale)
}

private struct Track: Identifiable, Decodable, Sendable {
    let id: String
    let name: String
    let status: Status
    let detail: String
    let copies: [String]
    let source: String
    let needsLogin: Bool?
    let hasOriginal: Bool?
    let title: String?
    let artists: [String]?
    let quality: String?
    let qualityDetail: String?
    let sourceFormat: String?

    private enum CodingKeys: String, CodingKey {
        case id, name, status, detail, copies, source, title, artists, quality
        case needsLogin = "needs_login"
        case hasOriginal = "has_original"
        case qualityDetail = "quality_detail"
        case sourceFormat = "source_format"
    }

    private var fileStem: String {
        let nameURL = URL(fileURLWithPath: name)
        let sourceExtension = URL(fileURLWithPath: source).pathExtension
        return nameURL.pathExtension.lowercased() == sourceExtension.lowercased()
            ? nameURL.deletingPathExtension().lastPathComponent
            : name
    }

    private var filenameMetadata: (title: String, artist: String?) {
        let stem = fileStem
        guard let separator = stem.range(of: " - ") ?? stem.range(of: "-") else {
            return (stem, nil)
        }
        let artist = stem[..<separator.lowerBound].trimmingCharacters(in: .whitespaces)
        let title = stem[separator.upperBound...].trimmingCharacters(in: .whitespaces)
        return (!title.isEmpty && !artist.isEmpty) ? (title, artist) : (stem, nil)
    }

    var displayTitle: String {
        let metadataTitle = title?.trimmingCharacters(in: .whitespacesAndNewlines)
        return metadataTitle.flatMap { $0.isEmpty ? nil : $0 } ?? filenameMetadata.title
    }

    var displayArtists: [String] {
        let supplied = (artists ?? []).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if !supplied.isEmpty { return supplied }
        guard let fallback = filenameMetadata.artist else { return [] }
        return fallback.split(whereSeparator: { ",，、;；".contains($0) })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    var sourceType: String {
        let value = (sourceFormat ?? URL(fileURLWithPath: source).pathExtension)
            .trimmingCharacters(in: .whitespaces).uppercased()
        return value.isEmpty ? "未知" : value
    }

    var qualityClass: String {
        if let quality, orderedQualities.contains(quality) { return quality }
        switch sourceType {
        case "MFLAC": return "无损"
        case "MGG": return "有损"
        default: return "未知"
        }
    }

    func matches(_ query: String, scope: SearchScope) -> Bool {
        guard !query.isEmpty else { return true }
        switch scope {
        case .all:
            return displayTitle.localizedStandardContains(query) ||
                displayArtists.contains(where: { $0.localizedStandardContains(query) })
        case .title:
            return displayTitle.localizedStandardContains(query)
        case .artists:
            return displayArtists.contains(where: { $0.localizedStandardContains(query) })
        }
    }

    func canConvert(with mode: Preset) -> Bool {
        let eligible = status == .pending || status == .failed || status == .repair || status == .originalReady
        return eligible && (mode != .qmc || !(hasOriginal ?? (status == .originalReady)))
    }
}

private struct Inventory: Decodable, Sendable {
    struct Paths: Decodable, Sendable {
        let source: String
        let export: String
        let configured: Bool
        let suggestedSource: String
        let suggestedExport: String
        let platform: String

        private enum CodingKeys: String, CodingKey {
            case source, export, configured, platform
            case suggestedSource = "suggested_source"
            case suggestedExport = "suggested_export"
        }
    }

    struct Summary: Decodable, Sendable {
        var success = 0
        var pending = 0
        var failed = 0
        var repair = 0
        var incomplete = 0
        var duplicates = 0
        var historical = 0
        var originalReady: Int?

        private enum CodingKeys: String, CodingKey {
            case success, pending, failed, repair, incomplete, duplicates, historical
            case originalReady = "original_ready"
        }

        var toProcess: Int { pending + failed + repair + (originalReady ?? 0) }
    }

    var exportDir = ""
    var report = ""
    var summary = Summary()
    var rows: [Track] = []
    var paths: Paths?

    private enum CodingKeys: String, CodingKey {
        case report, summary, rows, paths
        case exportDir = "export_dir"
    }
}

private struct BridgeEvent: Decodable, Sendable {
    let event: String
    let id: String?
    let status: String?
    let message: String?
    let done: Int?
    let total: Int?
}

private enum BridgeError: LocalizedError {
    case missingBackend
    case launchFailed
    case invalidResponse
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .missingBackend: "找不到转码组件，请从完整应用程序启动。"
        case .launchFailed: "无法启动本机转码组件。"
        case .invalidResponse: "转码组件返回的记录无法读取。请刷新后重试。"
        case .failed(let explanation): explanation
        }
    }
}

private enum Bridge {
    private struct ConvertRequest: Encodable {
        let selected: [String]
        let allow_qq_login: Bool
        let mode: String
    }

    private struct ConfigureRequest: Encodable {
        let source: String
        let export: String
    }

    private static func launcher(_ command: String, output: Pipe, input: Pipe? = nil) throws -> Process {
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        guard let resources = Bundle.main.resourceURL,
              let backend = Bundle.main.url(forResource: "studio", withExtension: "py") else {
            throw BridgeError.missingBackend
        }
        let python = resources.appendingPathComponent("runtime/\(architecture)/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw BridgeError.missingBackend
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-E", "-B", backend.path, command]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        if let input {
            process.standardInput = input
        } else {
            process.standardInput = FileHandle.nullDevice
        }
        return process
    }

    static func inventory() async throws -> Inventory {
        try await requestInventory("inventory")
    }

    static func configure(source: String, export: String) async throws -> Inventory {
        let payload = try JSONEncoder().encode(ConfigureRequest(source: source, export: export))
        return try await requestInventory("configure", payload: payload)
    }

    private static func requestInventory(_ command: String, payload: Data? = nil) async throws -> Inventory {
        try await Task.detached(priority: .userInitiated) {
            let output = Pipe()
            let input = payload.map { _ in Pipe() }
            let process = try launcher(command, output: output, input: input)
            do {
                try process.run()
            } catch {
                throw BridgeError.launchFailed
            }
            if let payload, let input {
                let writer = input.fileHandleForWriting
                do {
                    try writer.write(contentsOf: payload)
                    try writer.close()
                } catch {
                    try? writer.close()
                    throw BridgeError.launchFailed
                }
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                let event = try? JSONDecoder().decode(BridgeEvent.self, from: data)
                throw BridgeError.failed(event?.message ?? "无法读取本地歌曲与转码记录。")
            }
            do {
                return try JSONDecoder().decode(Inventory.self, from: data)
            } catch {
                throw BridgeError.invalidResponse
            }
        }.value
    }

    static func convert(
        selected: [String],
        mode: String,
        allowLogin: Bool
    ) -> AsyncThrowingStream<BridgeEvent, Error> {
        AsyncThrowingStream { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let output = Pipe()
                    let input = Pipe()
                    let process = try launcher("convert", output: output, input: input)
                    do {
                        try process.run()
                    } catch {
                        throw BridgeError.launchFailed
                    }
                    let payload = ConvertRequest(selected: selected, allow_qq_login: allowLogin, mode: mode)
                    let inputHandle = input.fileHandleForWriting
                    do {
                        try inputHandle.write(contentsOf: JSONEncoder().encode(payload))
                        try inputHandle.close()
                    } catch {
                        try? inputHandle.close()
                        throw BridgeError.launchFailed
                    }

                    let outputHandle = output.fileHandleForReading
                    let decoder = JSONDecoder()
                    var buffer = Data()
                    var finalError: String?
                    var completed = false
                    func deliver(_ line: Data) {
                        guard let event = try? decoder.decode(BridgeEvent.self, from: line) else { return }
                        if event.event == "error" { finalError = event.message ?? "无法完成本批转换。" }
                        if event.event == "complete" { completed = true }
                        continuation.yield(event)
                    }
                    while true {
                        let data = outputHandle.availableData
                        if data.isEmpty { break }
                        buffer.append(data)
                        while let endOfLine = buffer.firstIndex(of: 10) {
                            deliver(Data(buffer.prefix(upTo: endOfLine)))
                            buffer.removeSubrange(...endOfLine)
                        }
                    }
                    if !buffer.isEmpty { deliver(buffer) }
                    process.waitUntilExit()
                    if let finalError {
                        throw BridgeError.failed(finalError)
                    }
                    guard process.terminationStatus == 0, completed else {
                        throw BridgeError.failed("转换进程意外结束。请刷新台账查看已记录的结果。")
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}

private struct Batch: Identifiable {
    let id = UUID()
    let rows: [Track]
    let mode: Preset

    var needsLogin: Bool { rows.contains { $0.needsLogin != false } }
}

private struct DirectorySetup: Identifiable {
    let id = UUID()
    let source: String
    let export: String
    let suggestedSource: String
    let suggestedExport: String
}

private struct Notice {
    let label: String
    let isError: Bool
}

@MainActor
private final class StudioModel: ObservableObject {
    @Published var inventory = Inventory()
    @Published var filter = Filter.all
    @Published var mode = Preset.tunefree
    @Published var query = ""
    @Published var facets = LibraryFacets()
    @Published var grouping = Grouping.none
    @Published var titleSort = TitleSort.ascending
    @Published var selection = Set<String>()
    @Published var confirmation: Batch?
    @Published var directorySetup: DirectorySetup?
    @Published var directoryError: String?
    @Published var savingDirectories = false
    @Published var loading = false
    @Published var converting = false
    @Published var notice: Notice?
    @Published var refreshError: String?
    @Published var progress = 0
    @Published var total = 0
    @Published var trackProgress = ""
    @Published var rowResults: [String: BridgeEvent] = [:]
    private var titleKeys: [String: String] = [:]
    private var offeredDirectorySetup = false

    var changesFrozen: Bool {
        converting || confirmation != nil || directorySetup != nil || savingDirectories
    }

    var hasLibraryFilter: Bool {
        facets.hasFilters || !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var qualities: [String] {
        let known = Set(inventory.rows.map(\.qualityClass))
        return orderedQualities.filter { known.contains($0) }
    }

    var formats: [String] {
        Set(inventory.rows.map(\.sourceType)).sorted()
    }

    var performers: [String] {
        let names = Set(inventory.rows.flatMap(\.displayArtists))
        return names.map { (alphabeticKey($0), $0) }.sorted {
            let order = alphabeticOrder($0.0, $1.0)
            return order == .orderedSame ? $0.1 < $1.1 : order == .orderedAscending
        }.map(\.1)
    }

    var shown: [Track] {
        let searchTerm = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let matching = inventory.rows.filter { row in
            filter.matches(row.status) &&
                (facets.quality == nil || row.qualityClass == facets.quality) &&
                (facets.format == nil || row.sourceType == facets.format) &&
                (facets.artist == nil || row.displayArtists.contains(where: { $0 == facets.artist })) &&
                row.matches(searchTerm, scope: facets.scope)
        }
        return matching.sorted { a, b in
            let order = alphabeticOrder(titleKeys[a.id, default: ""], titleKeys[b.id, default: ""])
            if order == .orderedSame { return a.id < b.id }
            return titleSort == .ascending ? order == .orderedAscending : order == .orderedDescending
        }
    }

    func groups(for rows: [Track]) -> [TrackGroup] {
        guard grouping != .none else { return [TrackGroup(id: "songs", title: nil, rows: rows)] }
        let byGroup = Dictionary(grouping: rows) { track in
            switch grouping {
            case .none: ""
            case .quality: track.qualityClass
            case .format: track.sourceType
            case .artist: facets.artist ?? track.displayArtists.first ?? unknownArtist
            }
        }
        let headers: [String]
        if grouping == .quality {
            headers = orderedQualities.filter { byGroup[$0] != nil }
        } else {
            headers = byGroup.keys.map { (alphabeticKey($0), $0) }.sorted {
                let order = alphabeticOrder($0.0, $1.0)
                return order == .orderedSame ? $0.1 < $1.1 : order == .orderedAscending
            }.map(\.1)
        }
        return headers.map { TrackGroup(id: $0, title: $0, rows: byGroup[$0, default: []]) }
    }

    var selectable: Set<String> {
        Set(shown.filter { $0.canConvert(with: mode) }.map(\.id))
    }

    var selectedRows: [Track] {
        shown.filter { selection.contains($0.id) && $0.canConvert(with: mode) }
    }

    func count(_ filter: Filter) -> Int {
        inventory.rows.count(where: { filter.matches($0.status) })
    }

    func refresh() async {
        guard !loading, !changesFrozen else { return }
        loading = true
        defer { loading = false }
        do {
            let inventory = try await Bridge.inventory()
            guard !changesFrozen else { return }
            applyInventory(inventory)
            if inventory.paths?.configured == false && !offeredDirectorySetup {
                showDirectorySetup()
            }
        } catch {
            refreshError = error.localizedDescription
        }
    }

    private func applyInventory(_ inventory: Inventory) {
        titleKeys = Dictionary(inventory.rows.map { ($0.id, alphabeticKey($0.displayTitle)) },
                               uniquingKeysWith: { first, _ in first })
        self.inventory = inventory
        selection.formIntersection(selectable)
        refreshError = nil
    }

    func showDirectorySetup() {
        guard !changesFrozen else { return }
        offeredDirectorySetup = true
        directoryError = nil
        let paths = inventory.paths
        let defaultExport = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Music/QQ音乐转换").path
        let suggestedSource = paths?.suggestedSource ?? ""
        let suggestedExport = paths?.suggestedExport.isEmpty == false
            ? paths?.suggestedExport ?? defaultExport : defaultExport
        let source = paths?.source.isEmpty == false ? paths?.source ?? suggestedSource : suggestedSource
        let export = paths?.export.isEmpty == false ? paths?.export ?? suggestedExport : suggestedExport
        directorySetup = DirectorySetup(source: source, export: export,
                                        suggestedSource: suggestedSource, suggestedExport: suggestedExport)
    }

    func saveDirectories(source: String, export: String) {
        guard !savingDirectories, directorySetup != nil else { return }
        savingDirectories = true
        directoryError = nil
        Task {
            defer { savingDirectories = false }
            do {
                let result = try await Bridge.configure(source: source, export: export)
                guard result.paths?.configured == true else {
                    throw BridgeError.failed("保存后尚无法访问所选目录，请核对路径和权限。")
                }
                selection.removeAll()
                filter = .all
                facets = LibraryFacets()
                query = ""
                notice = nil
                applyInventory(result)
                directorySetup = nil
            } catch {
                directoryError = error.localizedDescription
            }
        }
    }

    func watch() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: .seconds(5))
        }
    }

    func toggleSelection(_ id: String) {
        guard !changesFrozen, selectable.contains(id) else { return }
        if !selection.insert(id).inserted {
            selection.remove(id)
        }
    }

    func toggleAll() {
        guard !changesFrozen else { return }
        let visible = selectable
        selection = visible.isSubset(of: selection) ? [] : visible
    }

    func prepareConversion() {
        let rows = selectedRows
        guard !rows.isEmpty, !changesFrozen else { return }
        confirmation = Batch(rows: rows, mode: mode)
    }

    func run(_ batch: Batch) {
        guard !converting else { return }
        confirmation = nil
        converting = true
        selection.removeAll()
        rowResults.removeAll()
        notice = nil
        total = batch.rows.count
        progress = 0
        trackProgress = "正在准备本批转换"
        let titles = Dictionary(uniqueKeysWithValues: batch.rows.map { ($0.id, $0.displayTitle) })
        Task {
            var failed = 0
            var completed = 0
            var completionMessage: String?
            do {
                let selected = batch.rows.map(\.id)
                for try await info in Bridge.convert(selected: selected, mode: batch.mode.rawValue, allowLogin: batch.needsLogin) {
                    if info.event == "progress" {
                        progress = info.done ?? progress
                        total = info.total ?? total
                        if let id = info.id {
                            if info.status == "failed" { failed += 1 }
                            if info.status == "success" || info.status == "original_ready" { completed += 1 }
                            rowResults[id] = info
                            let title = titles[id] ?? ""
                            trackProgress = title.isEmpty ? (info.message ?? "正在转换") : title
                        } else if let message = info.message {
                            trackProgress = message
                        }
                    } else if info.event == "complete" {
                        completionMessage = info.message
                    }
                }
                let label = completionMessage ?? "本批完成 \(completed) 首，失败 \(failed) 首。"
                notice = Notice(label: label, isError: failed > 0)
            } catch {
                notice = Notice(label: error.localizedDescription, isError: true)
            }
            converting = false
            await refresh()
        }
    }

    func open(_ path: String, isDirectory: Bool) {
        guard !path.isEmpty, FileManager.default.fileExists(atPath: path),
              NSWorkspace.shared.open(URL(fileURLWithPath: path, isDirectory: isDirectory)) else {
            notice = Notice(label: isDirectory ? "导出目录会在首次成功转换后创建。" : "当前转码台账还未生成。", isError: false)
            return
        }
    }
}

private struct Sidebar: View {
    @ObservedObject var model: StudioModel

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("歌曲")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 11)
                .padding(.bottom, 5)
            ForEach(Filter.allCases) { filter in
                let selected = model.filter == filter
                Button {
                    model.filter = filter
                    model.selection.removeAll()
                } label: {
                    HStack(spacing: 9) {
                        Image(systemName: filter.icon)
                            .frame(width: 18)
                        Text(filter.label)
                        Spacer(minLength: 4)
                        Text(model.count(filter), format: .number)
                            .monospacedDigit()
                            .foregroundStyle(selected ? Color.accentColor : .secondary)
                    }
                    .font(.system(size: 13, weight: selected ? .semibold : .regular))
                    .frame(minHeight: 36)
                    .padding(.horizontal, 10)
                    .contentShape(Rectangle())
                    .background(selected ? Color.accentColor.opacity(0.12) : .clear, in: .rect(cornerRadius: 8))
                    .foregroundStyle(selected ? Color.accentColor : .primary)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
                .disabled(model.changesFrozen)
            }
            Spacer()
            if model.inventory.summary.historical > 0 {
                Label("历史导出 \(model.inventory.summary.historical) 首", systemImage: "archivebox")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 9)
            }
            Button {
                model.open(model.inventory.report, isDirectory: false)
            } label: {
                Label("打开转码台账", systemImage: "doc.text")
                    .font(.system(size: 13))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(model.inventory.report.isEmpty)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 17)
        .frame(width: 169)
        .frame(maxHeight: .infinity, alignment: .topLeading)
        .background(.bar)
    }
}

private struct SummaryTile: View {
    let title: String
    let value: Int
    let icon: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(title, systemImage: icon)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(tint)
            Text(value, format: .number)
                .font(.system(size: 26, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: .rect(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.44), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct TrackRow: View {
    let track: Track
    let selected: Bool
    let eligible: Bool
    let batchResult: BridgeEvent?
    let toggle: () -> Void

    private var status: Status {
        batchResult?.status.flatMap(Status.init(rawValue:)) ?? track.status
    }

    var body: some View {
        HStack(spacing: 12) {
            Toggle(isOn: Binding(get: { selected }, set: { _ in toggle() })) {
                Text("选择 \(track.displayTitle)")
            }
            .toggleStyle(.checkbox)
            .labelsHidden()
            .disabled(!eligible)
            .frame(width: 18)
            VStack(alignment: .leading, spacing: 3) {
                Text(track.displayTitle)
                    .font(.system(size: 13, weight: selected ? .semibold : .regular))
                    .lineLimit(1)
                let performers = track.displayArtists
                Text(performers.isEmpty ? unknownArtist : performers.joined(separator: "、"))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                if let detail = batchResult?.message ?? (track.detail.isEmpty ? nil : track.detail) {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .help(track.name)
            VStack(alignment: .leading, spacing: 3) {
                Text(track.qualityClass)
                    .font(.system(size: 11, weight: .medium))
                if let detail = track.qualityDetail, !detail.isEmpty {
                    Text(detail)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }
            .lineLimit(1)
            .frame(width: qualityColumnWidth, alignment: .leading)
            .help(track.qualityDetail ?? track.qualityClass)
            Text(track.sourceType)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 65, alignment: .leading)
            Label(status.label, systemImage: status.icon)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(status.color)
                .frame(width: 96, alignment: .leading)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 6)
        .frame(minHeight: 48)
        .background(selected ? Color.accentColor.opacity(0.075) : .clear)
        .overlay(alignment: .bottom) {
            Divider().padding(.leading, 43)
        }
    }
}

private struct BatchConfirmation: View {
    let batch: Batch
    let cancel: () -> Void
    let begin: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Label("确认转换 \(batch.rows.count) 首歌曲", systemImage: "waveform")
                .font(.title2.weight(.semibold))
            Text(batch.mode.label)
                .foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(batch.rows) { row in
                        Text(row.displayTitle)
                            .font(.system(size: 13))
                            .lineLimit(1)
                            .help(row.name)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(11)
            }
            .frame(height: min(CGFloat(batch.rows.count * 25 + 13), 174))
            .background(Color(nsColor: .controlBackgroundColor), in: .rect(cornerRadius: 8))
            if batch.needsLogin {
                let needsCount = batch.rows.count(where: { $0.needsLogin != false })
                Text("本批 \(needsCount) 首需要解码密钥。点按「授权并开始」后，仅为这批指定歌曲使用本机 QQ 音乐当前登录，向 QQ 音乐官方服务请求密钥。")
                    .font(.system(size: 13))
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("取消", action: cancel)
                    .keyboardShortcut(.cancelAction)
                Button(batch.needsLogin ? "授权并开始" : "开始转换", action: begin)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(23)
        .frame(width: 522)
    }
}

private struct DirectorySettingsView: View {
    @ObservedObject var model: StudioModel
    let setup: DirectorySetup
    @State private var source: String
    @State private var export: String

    init(model: StudioModel, setup: DirectorySetup) {
        self.model = model
        self.setup = setup
        _source = State(initialValue: setup.source)
        _export = State(initialValue: setup.export)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("目录设置", systemImage: "folder")
                .font(.title2.weight(.semibold))
            pathField("QQ 音乐下载目录", path: $source, createDirectories: false)
            pathField("转换结果保存位置", path: $export, createDirectories: true)
            if let error = model.directoryError {
                Label(error, systemImage: "exclamationmark.circle.fill")
                    .foregroundStyle(.red)
                    .font(.system(size: 12))
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 9) {
                Button("恢复建议目录") {
                    if !setup.suggestedSource.isEmpty { source = setup.suggestedSource }
                    export = setup.suggestedExport
                }
                .disabled(model.savingDirectories || (source == setup.suggestedSource && export == setup.suggestedExport))
                Spacer()
                if model.savingDirectories {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("正在保存目录")
                }
                Button("取消") { model.directorySetup = nil }
                    .keyboardShortcut(.cancelAction)
                    .disabled(model.savingDirectories)
                Button("保存") {
                    model.saveDirectories(source: source.trimmingCharacters(in: .whitespacesAndNewlines),
                                          export: export.trimmingCharacters(in: .whitespacesAndNewlines))
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(model.savingDirectories ||
                          source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                          export.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(23)
        .frame(width: 545)
        .interactiveDismissDisabled(model.savingDirectories)
    }

    private func pathField(_ label: String, path: Binding<String>, createDirectories: Bool) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label)
                .font(.system(size: 13, weight: .medium))
            HStack(spacing: 8) {
                TextField("输入完整路径", text: path)
                    .accessibilityLabel(label)
                Button("选择…") {
                    chooseDirectory(label, path: path, createDirectories: createDirectories)
                }
                .accessibilityLabel("选择\(label)")
            }
            .disabled(model.savingDirectories)
        }
    }

    private func chooseDirectory(_ label: String, path: Binding<String>, createDirectories: Bool) {
        let panel = NSOpenPanel()
        panel.title = "选择\(label)"
        panel.prompt = "选择"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = createDirectories
        panel.allowsMultipleSelection = false

        let entered = path.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !entered.isEmpty {
            let expanded = (entered as NSString).expandingTildeInPath
            var folder = URL(fileURLWithPath: expanded, isDirectory: true)
            var isDirectory = ObjCBool(false)
            if !FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDirectory) || !isDirectory.boolValue {
                folder.deleteLastPathComponent()
            }
            panel.directoryURL = folder
        }
        panel.begin { result in
            guard result == .OK, let address = panel.url else { return }
            path.wrappedValue = address.path
        }
    }
}

private struct StudioView: View {
    private static let coverImage = Bundle.main.image(forResource: "AppCover")
    @StateObject private var model = StudioModel()
    @FocusState private var searchFocused: Bool

    private var allSelected: Bool {
        !model.selectable.isEmpty && model.selectable.isSubset(of: model.selection)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HStack(spacing: 0) {
                Sidebar(model: model)
                Divider()
                workspace
            }
            Divider()
            footer
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .frame(minWidth: 835, minHeight: 590)
        .task { await model.watch() }
        .sheet(item: $model.confirmation) { batch in
            BatchConfirmation(batch: batch, cancel: { model.confirmation = nil }, begin: { model.run(batch) })
        }
        .sheet(item: $model.directorySetup) { setup in
            DirectorySettingsView(model: model, setup: setup)
        }
    }

    private var header: some View {
        HStack(spacing: 13) {
            Group {
                if let coverImage = Self.coverImage {
                    Image(nsImage: coverImage)
                        .resizable()
                        .interpolation(.high)
                        .scaledToFit()
                        .clipShape(.rect(cornerRadius: 12))
                } else {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(Color.accentColor.gradient)
                        Image(systemName: "waveform")
                            .font(.system(size: 24, weight: .semibold))
                            .foregroundStyle(.white)
                    }
                }
            }
            .frame(width: 46, height: 46)
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text("QQ 音乐转码")
                    .font(.system(size: 23, weight: .semibold))
                Text("筛选歌曲、导出音频、查询转换结果")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await model.refresh() }
            } label: {
                if model.loading {
                    ProgressView()
                        .controlSize(.small)
                        .frame(width: 15, height: 15)
                } else {
                    Image(systemName: "arrow.clockwise")
                        .frame(width: 15, height: 15)
                }
            }
            .help("刷新歌曲")
            .accessibilityLabel("刷新歌曲")
            .keyboardShortcut("r", modifiers: .command)
            .disabled(model.loading || model.changesFrozen)
            Button {
                model.showDirectorySetup()
            } label: {
                Label("目录设置", systemImage: "gearshape")
            }
            .disabled(model.changesFrozen || model.loading)
            Button {
                model.open(model.inventory.exportDir, isDirectory: true)
            } label: {
                Label("打开导出目录", systemImage: "folder")
            }
            .disabled(model.inventory.exportDir.isEmpty)
        }
        .buttonStyle(.bordered)
        .controlSize(.regular)
        .padding(.horizontal, 22)
        .padding(.vertical, 16)
    }

    private var workspace: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(spacing: 10) {
                let summary = model.inventory.summary
                SummaryTile(title: "待处理", value: summary.toProcess, icon: "tray", tint: .accentColor)
                SummaryTile(title: "已成功", value: summary.success, icon: "checkmark.circle", tint: .green)
                SummaryTile(title: "失败", value: summary.failed, icon: "exclamationmark.circle", tint: .red)
                SummaryTile(title: "重复副本", value: summary.duplicates, icon: "square.on.square", tint: .secondary)
            }
            if let error = model.refreshError {
                noticeView(Notice(label: error, isError: true))
            }
            if let notice = model.notice {
                noticeView(notice)
            }
            if model.inventory.paths?.configured == false {
                HStack {
                    Label("选择 QQ 音乐下载目录和导出位置后即可使用。", systemImage: "folder")
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("设置目录") { model.showDirectorySetup() }
                        .disabled(model.changesFrozen)
                }
                .font(.system(size: 13))
            }
            libraryControls
                .disabled(model.changesFrozen)
            table
        }
        .padding(.horizontal, 17)
        .padding(.vertical, 15)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onChange(of: model.query) { _, _ in
            model.selection.formIntersection(model.selectable)
        }
        .onChange(of: model.facets) { _, _ in
            model.selection.formIntersection(model.selectable)
        }
    }

    private var libraryControls: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 9) {
                Text(model.filter.label)
                    .font(.system(size: 16, weight: .semibold))
                    .accessibilityAddTraits(.isHeader)
                Text("\(model.shown.count) 首")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .font(.system(size: 12))
                Spacer()
                Picker("搜索范围", selection: $model.facets.scope) {
                    ForEach(SearchScope.allCases) { scope in
                        Text(scope.rawValue).tag(scope)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 143)
                searchField
            }
            HStack(spacing: 7) {
                facetMenu("音质", "全部音质", selection: $model.facets.quality, values: model.qualities, width: 93)
                facetMenu("格式", "全部格式", selection: $model.facets.format, values: model.formats, width: 103)
                facetMenu("歌手", "全部歌手", selection: $model.facets.artist, values: model.performers, width: 132)
                if model.facets.hasFilters {
                    Button("清除筛选") {
                        model.facets = LibraryFacets(scope: model.facets.scope)
                    }
                    .buttonStyle(.borderless)
                }
                Spacer(minLength: 1)
                Picker("分组", selection: $model.grouping) {
                    ForEach(Grouping.allCases) { group in
                        Text(group == .none ? group.rawValue : "按\(group.rawValue)分组").tag(group)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .fixedSize()
                .help("按音质、格式或主要歌手分组")
                Picker("按歌名首字母排序", selection: $model.titleSort) {
                    ForEach(TitleSort.allCases) { order in
                        Text(order.rawValue).tag(order)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .fixedSize()
                .help("按歌名首字母排序，中文按拼音")
            }
            .controlSize(.small)
            .font(.system(size: 12))
        }
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)
            TextField(model.facets.scope.prompt, text: $model.query)
                .textFieldStyle(.plain)
                .focused($searchFocused)
            if !model.query.isEmpty {
                Button {
                    model.query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("清除搜索")
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .frame(width: 192)
        .background(Color(nsColor: .textBackgroundColor), in: .rect(cornerRadius: 7))
        .overlay {
            RoundedRectangle(cornerRadius: 7)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.44), lineWidth: 1)
        }
    }

    private func facetMenu(
        _ name: String, _ clearLabel: String, selection: Binding<String?>, values: [String], width: CGFloat
    ) -> some View {
        Menu {
            Button {
                selection.wrappedValue = nil
            } label: {
                if selection.wrappedValue == nil {
                    Label(clearLabel, systemImage: "checkmark")
                } else {
                    Text(clearLabel)
                }
            }
            if !values.isEmpty { Divider() }
            ForEach(values, id: \.self) { value in
                Button {
                    selection.wrappedValue = value
                } label: {
                    if selection.wrappedValue == value {
                        Label(value, systemImage: "checkmark")
                    } else {
                        Text(value)
                    }
                }
            }
        } label: {
            Text(selection.wrappedValue.map { "\(name) · \($0)" } ?? clearLabel)
                .lineLimit(1)
                .frame(width: width - 24, alignment: .leading)
        }
        .help(selection.wrappedValue ?? clearLabel)
        .accessibilityLabel("\(name)筛选")
    }

    private var table: some View {
        let visibleTracks = model.shown
        return VStack(spacing: 0) {
            HStack(spacing: 12) {
                Toggle(isOn: Binding(get: { allSelected }, set: { _ in model.toggleAll() })) {
                    Text(allSelected ? "取消全选此视图" : "全选此视图")
                }
                .toggleStyle(.checkbox)
                .labelsHidden()
                .disabled(model.selectable.isEmpty || model.changesFrozen)
                .frame(width: 18)
                .help(allSelected ? "取消全选此视图" : "全选此视图")
                Text("歌曲")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("音质")
                    .frame(width: qualityColumnWidth, alignment: .leading)
                Text("源格式")
                    .frame(width: 65, alignment: .leading)
                Text("状态")
                    .frame(width: 96, alignment: .leading)
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 13)
            .frame(height: 33)
            .background(Color(nsColor: .controlBackgroundColor))
            Divider()
            if visibleTracks.isEmpty {
                VStack(spacing: 9) {
                    if model.loading && model.inventory.rows.isEmpty {
                        ProgressView()
                        Text("正在读取歌曲")
                            .foregroundStyle(.secondary)
                    } else {
                        Image(systemName: model.hasLibraryFilter ? "magnifyingglass" : model.filter.icon)
                            .font(.system(size: 25, weight: .light))
                        Text(model.hasLibraryFilter ? "没有匹配的歌曲" : model.filter.emptyLabel)
                            .font(.system(size: 14, weight: .medium))
                    }
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(model.groups(for: visibleTracks)) { group in
                            if let title = group.title {
                                groupHeader(title, count: group.rows.count)
                            }
                            ForEach(group.rows) { track in
                                TrackRow(
                                    track: track,
                                    selected: model.selection.contains(track.id),
                                    eligible: !model.changesFrozen && track.canConvert(with: model.mode),
                                    batchResult: model.converting ? model.rowResults[track.id] : nil
                                ) {
                                    model.toggleSelection(track.id)
                                }
                            }
                        }
                    }
                }
                .contentMargins(.trailing, 1, for: .scrollIndicators)
            }
        }
        .frame(maxHeight: .infinity)
        .background(Color(nsColor: .textBackgroundColor))
        .clipShape(.rect(cornerRadius: 9))
        .overlay {
            RoundedRectangle(cornerRadius: 9)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.5), lineWidth: 1)
                .allowsHitTesting(false)
        }
    }

    private func groupHeader(_ title: String, count: Int) -> some View {
        HStack(spacing: 9) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .lineLimit(1)
            Text(count, format: .number)
                .font(.caption)
                .monospacedDigit()
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 14)
        .frame(height: 30)
        .background(.quaternary.opacity(0.55))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private var footer: some View {
        HStack(spacing: 16) {
            if model.converting {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(model.trackProgress)
                            .lineLimit(1)
                        Spacer()
                        Text("\(model.progress) / \(model.total)")
                            .monospacedDigit()
                    }
                    .font(.system(size: 12, weight: .medium))
                    ProgressView(value: Double(model.progress), total: Double(max(1, model.total)))
                        .accessibilityLabel("本批进度")
                }
                .frame(maxWidth: 490)
                Spacer()
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text("导出预设")
                            .font(.system(size: 12, weight: .medium))
                        Picker("导出预设", selection: $model.mode) {
                            ForEach(Preset.allCases) { preset in
                                Text(preset.label).tag(preset)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.menu)
                        .fixedSize()
                        .onChange(of: model.mode) { _, _ in
                            model.selection.formIntersection(model.selectable)
                        }
                    }
                    Text("预设共用 TuneFree 解密，分别采用对应的导出设置。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !model.selectedRows.isEmpty {
                    Text("已选 \(model.selectedRows.count) 首")
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
            Button {
                model.prepareConversion()
            } label: {
                Label(model.converting ? "正在转换" : "转换所选", systemImage: "waveform")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(model.selectedRows.isEmpty || model.converting)
        }
        .padding(.horizontal, 21)
        .padding(.vertical, 13)
        .frame(minHeight: 72)
        .background(.bar)
    }

    private func noticeView(_ notice: Notice) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: notice.isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundStyle(notice.isError ? .red : .green)
            Text(notice.label)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.system(size: 12))
        .padding(8)
        .background((notice.isError ? Color.red : .green).opacity(0.08), in: .rect(cornerRadius: 7))
    }
}

@main
struct QQMusicStudio: App {
    var body: some Scene {
        Window("QQ 音乐转码", id: "studio") {
            StudioView()
                .tint(Color(red: 0.28, green: 0.35, blue: 0.84))
        }
        .defaultSize(width: 1000, height: 675)
        .windowResizability(.contentMinSize)
    }
}
