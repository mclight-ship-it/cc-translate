import AppKit
import Combine

@MainActor
final class AboutModel: ObservableObject {
    enum Phase: Equatable { case idle, loading, loaded }
    enum CopyStatus: Equatable { case copied, failed }
    enum Page: Hashable { case application, licenses }
    @Published var page: Page = .application
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var overview = AboutOverview()
    @Published private(set) var documentText: String?
    @Published private(set) var documentError: AboutResourceError?
    @Published private(set) var documentBusy = false
    @Published private(set) var copyStatus: CopyStatus?
    @Published private(set) var showingSupport = false
    @Published private(set) var supportImage: NSImage?
    @Published private(set) var supportError: AboutResourceError?
    @Published private(set) var supportBusy = false
    @Published var selectedDocument: String? {
        didSet { if selectedDocument != oldValue && !updatingSelection { loadDocument() } }
    }

    private let resources: any AboutResourceReading
    private let writeClipboard: (String) -> Bool
    private var generation = UUID()
    private var documentGeneration = UUID()
    private var supportGeneration = UUID()
    private(set) var loadTask: Task<Void, Never>?
    private(set) var documentTask: Task<Void, Never>?
    private(set) var supportTask: Task<Void, Never>?
    private var open = false
    private var updatingSelection = false

    init(resources: any AboutResourceReading = AboutBundleResources(),
         writeClipboard: @escaping (String) -> Bool = {
             NSPasteboard.general.clearContents()
             return NSPasteboard.general.setString($0, forType: .string)
         }) {
        self.resources = resources
        self.writeClipboard = writeClipboard
    }

    func openResources() {
        if !open {
            updatingSelection = true
            selectedDocument = nil
            updatingSelection = false
            page = .application
        }
        open = true
        reload()
    }

    func reload() {
        guard open else { return }
        self.generation = UUID()
        let generation = self.generation
        loadTask?.cancel()
        cancelDocument()
        dismissSupport()
        phase = .loading
        overview = AboutOverview()
        copyStatus = nil
        let resources = self.resources
        loadTask = Task { [weak self] in
            guard !Task.isCancelled else { return }
            let worker = Task.detached(priority: .userInitiated) { resources.overview() }
            let result = await withTaskCancellationHandler { await worker.value } onCancel: { worker.cancel() }
            guard let self, self.open, self.generation == generation, !Task.isCancelled else { return }
            self.overview = result
            self.updatingSelection = true
            if !result.documents.contains(where: { $0.id == self.selectedDocument }) { self.selectedDocument = nil }
            self.updatingSelection = false
            self.phase = .loaded
            self.loadTask = nil
            if self.selectedDocument != nil { self.loadDocument() }
        }
    }

    func loadDocument() {
        cancelDocument()
        guard open, phase == .loaded, let selectedDocument else { return }
        guard let document = overview.documents.first(where: { $0.id == selectedDocument }) else {
            documentError = .invalidLocation
            return
        }
        documentBusy = true
        let generation = documentGeneration
        let resources = self.resources
        documentTask = Task { [weak self] in
            guard !Task.isCancelled else { return }
            let worker = Task.detached(priority: .userInitiated) { resources.license(document) }
            let result = await withTaskCancellationHandler { await worker.value } onCancel: { worker.cancel() }
            guard let self, self.open, self.documentGeneration == generation, !Task.isCancelled else { return }
            self.documentBusy = false
            self.documentTask = nil
            switch result {
            case .success(let text): self.documentText = text
            case .failure(let error): self.documentError = error
            }
        }
    }

    func close() {
        open = false
        generation = UUID()
        loadTask?.cancel()
        loadTask = nil
        cancelDocument()
        dismissSupport()
        updatingSelection = true
        selectedDocument = nil
        updatingSelection = false
        overview = AboutOverview()
        page = .application
        copyStatus = nil
        phase = .idle
    }

    private func cancelDocument() {
        documentGeneration = UUID()
        documentTask?.cancel()
        documentTask = nil
        documentText = nil
        documentError = nil
        documentBusy = false
    }

    func showSupport() {
        guard open, phase == .loaded, !showingSupport else { return }
        showingSupport = true
        loadSupport()
    }

    func loadSupport() {
        guard open, showingSupport else { return }
        cancelSupport()
        supportBusy = true
        let generation = supportGeneration
        let resources = self.resources
        let expected = overview.source?.resource_hashes?[AboutBundleResources.supportImagePath]
        supportTask = Task { [weak self] in
            guard !Task.isCancelled else { return }
            let worker = Task.detached(priority: .userInitiated) {
                resources.supportImage(expectedSHA256: expected)
            }
            let result = await withTaskCancellationHandler { await worker.value } onCancel: { worker.cancel() }
            guard let self, self.open, self.showingSupport,
                  self.supportGeneration == generation, !Task.isCancelled else { return }
            self.supportBusy = false
            self.supportTask = nil
            switch result {
            case .success(let data):
                guard let image = NSImage(data: data), image.isValid else {
                    self.supportError = .invalidImage
                    return
                }
                self.supportImage = image
            case .failure(let error): self.supportError = error
            }
        }
    }

    func dismissSupport() {
        showingSupport = false
        cancelSupport()
    }

    private func cancelSupport() {
        supportGeneration = UUID()
        supportTask?.cancel()
        supportTask = nil
        supportImage = nil
        supportError = nil
        supportBusy = false
    }

    func copyInformation(using presentation: ProbeModel) {
        guard phase == .loaded else { return }
        let absent = presentation.text("Not supplied in bundle", "包内未提供")
        let info = overview.info
        let source = overview.source
        let content = [
            "CC Translate",
            presentation.text("Bundle name", "包名称") + ": " + (info?.name ?? absent),
            presentation.text("Version", "版本") + ": " + (info?.version ?? absent),
            presentation.text("Build", "构建号") + ": " + (info?.build ?? absent),
            presentation.text("Bundle identifier", "包标识") + ": " + (info?.identifier ?? absent),
            presentation.text("Recorded source commit", "记录的源码提交") + ": " + (source?.source_commit ?? absent),
            presentation.text("Recorded signing declaration", "记录的签名声明") + ": " + (source?.signing ?? absent),
            presentation.text("This information comes from bundled metadata; signature and notarization were not verified here.",
                              "这些信息来自随包元数据；此界面未验证签名或公证。")
        ] + overview.issues.map { $0.resource + ": " + message(for: $0.error, using: presentation) }
        copyStatus = writeClipboard(content.joined(separator: "\n")) ? .copied : .failed
    }

    func message(for error: AboutResourceError, using model: ProbeModel) -> String {
        switch error {
        case .unavailable: return model.text("The application bundle could not be located.", "无法定位应用程序包。")
        case .missing: return model.text("This resource is missing from the application bundle.", "应用程序包中缺少此资源。")
        case .unreadable: return model.text("This bundled resource could not be read. Check the application package and retry.", "无法读取此随包资源。请检查应用程序包后重试。")
        case .linkedResource: return model.text("Linked resources are not read. Use a complete application package.", "不读取符号链接资源。请使用完整的应用程序包。")
        case .invalidLocation: return model.text("This is not a regular resource within the expected bundle location.", "此资源不是预期包路径内的普通文件。")
        case .invalidMetadata: return model.text("The bundled metadata is malformed. No version or build claim was inferred.", "随包元数据格式损坏，未推断版本或构建信息。")
        case .unsupportedSchema: return model.text("This build manifest format is not supported.", "尚不支持此构建清单格式。")
        case .invalidEncoding: return model.text("This resource is not valid UTF-8 text. No replacement or partial text is shown.", "此资源不是有效的 UTF-8 文本，未替换或显示部分内容。")
        case .invalidImage: return model.text("The bundled support image could not be decoded. Check the application package and retry.", "无法解码随包的支持作者图片。请检查应用程序包后重试。")
        case .emptyDocument: return model.text("This bundled document is empty.", "此随包文档为空。")
        case .tooLarge: return model.text("This resource exceeds the local viewer limit. No truncated text is shown.", "此资源超出本地阅读器限制，未显示截断文本。")
        case .checksumMismatch: return model.text("This file differs from the bundled manifest. No partial or altered content is shown; check the package and retry.", "此文件与随包清单不一致。未显示不完整或改动后的内容，请检查应用程序包后重试。")
        }
    }
}
