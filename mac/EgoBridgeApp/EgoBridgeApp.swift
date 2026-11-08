import SwiftUI
import AppKit

// Original 22×18 monochrome mark: browser chrome + an Agent pointer.
// State badges retain the silhouette, so the app stays recognisable in the menu bar.
enum BridgeIcon {
    static func make(state: String = "connected") -> NSImage {
        let image = NSImage(size: NSSize(width: 22, height: 18), flipped: false) { _ in
            NSColor.black.set()
            let chrome = NSBezierPath()
            chrome.lineWidth = 1.5
            chrome.lineCapStyle = .round
            chrome.lineJoinStyle = .round
            chrome.move(to: NSPoint(x: 7.4, y: 3.5))
            chrome.line(to: NSPoint(x: 3.5, y: 3.5))
            chrome.curve(to: NSPoint(x: 1.5, y: 5.5), controlPoint1: NSPoint(x: 2.2, y: 3.5), controlPoint2: NSPoint(x: 1.5, y: 4.2))
            chrome.line(to: NSPoint(x: 1.5, y: 13.5))
            chrome.curve(to: NSPoint(x: 3.5, y: 15.5), controlPoint1: NSPoint(x: 1.5, y: 14.8), controlPoint2: NSPoint(x: 2.2, y: 15.5))
            chrome.line(to: NSPoint(x: 12.5, y: 15.5))
            chrome.move(to: NSPoint(x: 1.5, y: 11.8))
            chrome.line(to: NSPoint(x: 12.5, y: 11.8))
            chrome.stroke()
            NSBezierPath(ovalIn: NSRect(x: 3.7, y: 13, width: 1.2, height: 1.2)).fill()
            let cursor = NSBezierPath()
            cursor.move(to: NSPoint(x: 9.3, y: 9.7))
            for point in [NSPoint(x: 9.3, y: 1.5), NSPoint(x: 11.5, y: 3.9),
                          NSPoint(x: 13.5, y: 0.4), NSPoint(x: 15.1, y: 1.3),
                          NSPoint(x: 13.2, y: 4.6), NSPoint(x: 16.6, y: 4.7)] {
                cursor.line(to: point)
            }
            cursor.close(); cursor.fill()
            let badge = NSBezierPath()
            badge.lineWidth = 1.5; badge.lineCapStyle = .round; badge.lineJoinStyle = .round
            switch state {
            case "connected":
                badge.move(to: NSPoint(x: 16, y: 13.5)); badge.line(to: NSPoint(x: 17.7, y: 11.8))
                badge.line(to: NSPoint(x: 21, y: 15.5))
            case "connecting":
                badge.appendArc(withCenter: NSPoint(x: 18.5, y: 13.7), radius: 2.5, startAngle: 30, endAngle: 310)
                badge.move(to: NSPoint(x: 18.5, y: 15)); badge.line(to: NSPoint(x: 18.5, y: 13.5))
                badge.line(to: NSPoint(x: 20, y: 13.5))
            case "error":
                badge.move(to: NSPoint(x: 18.5, y: 16)); badge.line(to: NSPoint(x: 18.5, y: 13.2))
                NSBezierPath(ovalIn: NSRect(x: 17.8, y: 10.7, width: 1.4, height: 1.4)).fill()
            default:
                badge.move(to: NSPoint(x: 16.4, y: 13.5)); badge.line(to: NSPoint(x: 20.6, y: 13.5))
            }
            badge.stroke()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "ego bridge"
        return image
    }
}

struct AgentSpace: Identifiable {
    var id: Int
    var name: String
    var ownership: String
    var recentTitle: String
}

struct CoreStatus {
    var state = "off"
    var detail = "启动中…"
    var paired = false
    var enabled = false
    var host: String?
    var port: Int?
    var egoRunning: Bool?
    var activeRuns = 0
    var connectedSince: Date?
    var operationError: String?
    var activityState = "idle"
    var agentSpaces: [AgentSpace] = []
    var agentSpaceTotal = 0
}

final class CoreProcess: ObservableObject {
    static let shared = CoreProcess()
    @Published var status = CoreStatus()
    @Published var coreMissing = false
    @Published var busy = false
    @Published var operationSucceeded = 0
    var onStatus: (() -> Void)?
    private var proc: Process?
    private var stdinHandle: FileHandle?
    private let reader = DispatchQueue(label: "ego-bridge.reader")
    private var pending = false
    private var quitting = false

    func start() {
        guard proc == nil else { return }
        guard let url = Bundle.main.url(forResource: "bridge_core", withExtension: "py"),
              let py = ["/usr/local/bin/python3", "/opt/homebrew/bin/python3", "/usr/bin/python3"]
                .first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            coreMissing = true
            status.state = "error"
            status.detail = "缺少 Python 3 或 App 核心文件，请重新构建 App。"
            return
        }
        let p = Process(), input = Pipe(), output = Pipe()
        p.executableURL = URL(fileURLWithPath: py)
        // Python must not mutate signed bundle resources by creating __pycache__.
        p.arguments = ["-B", url.path]
        p.standardInput = input
        p.standardOutput = output
        let logDir = NSHomeDirectory() + "/Library/Logs"
        try? FileManager.default.createDirectory(atPath: logDir, withIntermediateDirectories: true)
        let logPath = logDir + "/ego-bridge-core.log"
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        let log = FileHandle(forWritingAtPath: logPath) ?? .nullDevice
        _ = try? log.seekToEnd()
        p.standardError = log
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                self.proc = nil
                self.stdinHandle = nil
                self.busy = false
                self.status.state = "error"
                self.status.detail = "核心进程已退出，请重新打开 App。"
                self.onStatus?()
            }
        }
        do { try p.run() } catch {
            status.state = "error"
            status.detail = "核心启动失败：\(error.localizedDescription)"
            return
        }
        proc = p
        stdinHandle = input.fileHandleForWriting
        reader.async { [weak self] in
            var buffer = Data()
            while true {
                let data = output.fileHandleForReading.availableData
                if data.isEmpty { break }
                buffer.append(data)
                while let nl = buffer.firstIndex(of: 0x0A) {
                    let line = Data(buffer[..<nl])
                    buffer.removeSubrange(...nl)
                    if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                       obj["ev"] as? String == "status" {
                        DispatchQueue.main.async { self?.apply(obj) }
                    }
                }
            }
        }
        // One-time migration. The core owns persistence for both CLI and App from now on.
        let settings = NSHomeDirectory() + "/Library/Application Support/ego-bridge/settings.json"
        if !FileManager.default.fileExists(atPath: settings),
           UserDefaults.standard.bool(forKey: "egoBridgeEnabled") {
            send("start")
        }
    }

    private func apply(_ obj: [String: Any]) {
        var s = CoreStatus()
        s.state = obj["state"] as? String ?? "off"
        s.detail = obj["detail"] as? String ?? ""
        s.paired = obj["paired"] as? Bool ?? false
        s.enabled = obj["enabled"] as? Bool ?? false
        s.host = obj["host"] as? String
        s.port = obj["port"] as? Int
        s.egoRunning = obj["egoRunning"] as? Bool
        s.activeRuns = obj["activeRuns"] as? Int ?? 0
        s.operationError = obj["operationError"] as? String
        if let activity = obj["activity"] as? [String: Any] {
            s.activityState = activity["state"] as? String ?? "idle"
            s.agentSpaceTotal = activity["total"] as? Int ?? 0
            s.agentSpaces = (activity["spaces"] as? [[String: Any]] ?? []).compactMap { item in
                guard let id = item["id"] as? Int else { return nil }
                return AgentSpace(id: id, name: item["name"] as? String ?? "未命名空间",
                                  ownership: item["ownership"] as? String ?? "unknown",
                                  recentTitle: item["recentTitle"] as? String ?? "")
            }
        }
        if let ts = obj["connectedSince"] as? Double { s.connectedSince = Date(timeIntervalSince1970: ts) }
        status = s
        // Completion is explicit; a timer or a pre-existing paired=true is not success.
        if pending, let done = obj["commandDone"] as? Bool, done {
            pending = false
            busy = false
            if s.operationError == nil { operationSucceeded += 1 }
        }
        onStatus?()
    }

    func send(_ command: String, extra: [String: Any] = [:], operation: Bool = false) {
        guard let stdinHandle else { return }
        var obj = extra
        obj["cmd"] = command
        obj["source"] = "app"
        if operation { pending = true; busy = true; obj["operation"] = true }
        guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        do { try stdinHandle.write(contentsOf: data + Data([0x0A])) } catch {
            busy = false
            status.operationError = "控制通道已关闭，请重新打开 App。"
        }
    }

    func quit() {
        guard !quitting else { return }
        quitting = true
        send("quit")
        guard let proc else { return }
        // App exits immediately; ensure the child has received quit before closing its pipes.
        let deadline = Date().addingTimeInterval(2)
        while proc.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
        if proc.isRunning { proc.terminate() }
    }
}

// AppKit owns the popover size. MenuBarExtra's cached window size leaves a white
// strip when its SwiftUI content changes height during connect/reconnect.
final class PanelController: NSHostingController<AnyView> {
    var onSize: ((NSSize) -> Void)?
    override func viewDidLayout() {
        super.viewDidLayout()
        let size = view.fittingSize
        guard size.height > 0 else { return }
        DispatchQueue.main.async { [weak self] in self?.onSize?(size) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private var item: NSStatusItem!
    private let popover = NSPopover()

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: 28)
        item.button?.target = self
        item.button?.action = #selector(togglePanel)
        item.button?.setAccessibilityLabel("ego bridge")
        popover.behavior = .transient
        popover.delegate = self
        popover.animates = false
        let controller = PanelController(rootView: AnyView(PanelView().environmentObject(CoreProcess.shared)))
        controller.sizingOptions = [.preferredContentSize]
        controller.onSize = { [weak self] size in
            guard let self else { return }
            let next = NSSize(width: 360, height: ceil(size.height))
            if self.popover.contentSize != next { self.popover.contentSize = next }
        }
        popover.contentViewController = controller
        popover.contentSize = controller.view.fittingSize
        CoreProcess.shared.onStatus = { [weak self] in self?.updateIcon() }
        CoreProcess.shared.start()
        updateIcon()
    }

    private func updateIcon() {
        let s = CoreProcess.shared.status
        item.button?.image = BridgeIcon.make(state: s.state)
        item.button?.toolTip = "ego bridge · \(s.detail)"
    }

    func popoverDidShow(_ notification: Notification) {
        CoreProcess.shared.send("watch_activity", extra: ["enabled": true])
    }

    func popoverDidClose(_ notification: Notification) {
        CoreProcess.shared.send("watch_activity", extra: ["enabled": false])
    }

    @objc private func togglePanel() {
        if popover.isShown { popover.performClose(nil); return }
        guard let button = item.button else { return }
        CoreProcess.shared.send("refresh")
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
    }

    func applicationWillTerminate(_ notification: Notification) { CoreProcess.shared.quit() }
}

#if !PREVIEW
@main
struct EgoBridgeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    var body: some Scene { Settings { EmptyView() } }
}
#endif

// Whole rows are hit targets with native keyboard focus and explicit hover/press feedback.
private struct ActionStyle: ButtonStyle {
    var destructive = false
    var compact = false
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovered = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(destructive ? Color.red : Color.primary)
            .padding(.horizontal, compact ? 8 : 10).padding(.vertical, compact ? 6 : 9)
            .frame(maxWidth: compact ? nil : .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .background(RoundedRectangle(cornerRadius: 7).fill(
                Color.primary.opacity(!isEnabled ? 0 : configuration.isPressed ? 0.12 : hovered ? 0.065 : 0)))
            .opacity(isEnabled ? 1 : 0.45)
            .onHover { hovered = $0 }
    }
}

struct PanelView: View {
    @EnvironmentObject var core: CoreProcess
    @State private var showEdit = false
    @State private var armUnpair = false
    init(initialEdit: Bool = false) {
        _showEdit = State(initialValue: initialEdit)
    }
    private var editing: Bool { showEdit || !core.status.paired }
    private var tint: Color {
        switch core.status.state {
        case "connected": return Color(nsColor: .systemGreen)
        case "connecting": return Color(nsColor: .systemOrange)
        case "error": return .red
        default: return .secondary
        }
    }
    private var title: String {
        if showEdit { return "编辑连接" }
        if !core.status.paired { return "连接你的 devbox" }
        switch core.status.state {
        case "connected": return "连接已就绪"
        case "connecting": return "正在连接"
        case "error": return "连接需要处理"
        default: return "远端控制已暂停"
        }
    }
    private var detail: String {
        if showEdit { return "更新设备地址，或使用新配对码。" }
        if core.coreMissing || core.status.state == "error" { return core.status.detail }
        if !core.status.paired { return "配对一次，之后打开即连。" }
        switch core.status.state {
        case "connected": return core.status.activeRuns > 0
            ? "正在执行 \(core.status.activeRuns) 个远端任务" : "devbox 可以使用这台 Mac 的浏览器。"
        case "connecting": return core.status.detail
        default: return "配对已保留，开启开关即可恢复。"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(nsImage: BridgeIcon.make(state: core.status.state))
                    .renderingMode(.template).resizable().scaledToFit().foregroundStyle(.primary)
                    .frame(width: 32, height: 32)
                VStack(alignment: .leading, spacing: 2) {
                    Text("ego bridge").font(.system(size: 16, weight: .semibold))
                    Text("Mac 与 devbox，保持连接").font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("远端控制", isOn: Binding(get: { core.status.enabled },
                                               set: { core.send($0 ? "start" : "stop") }))
                    .labelsHidden().toggleStyle(.switch).controlSize(.small)
                    .disabled(!core.status.paired || core.coreMissing || core.busy)
                    .help("暂停远端控制，保留配对")
            }
            .padding(.bottom, 20)

            HStack(spacing: 7) {
                Image(systemName: showEdit ? "slider.horizontal.3" : core.status.state == "connected" ? "checkmark.circle.fill" :
                        core.status.state == "connecting" ? "arrow.triangle.2.circlepath" :
                        core.status.state == "error" ? "exclamationmark.circle.fill" : "circle")
                    .foregroundStyle(showEdit ? Color.secondary : tint).font(.system(size: 15, weight: .medium))
                Text(title).font(.system(size: 18, weight: .semibold))
                Spacer()
            }
            Text(detail).font(.system(size: 12)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6).padding(.bottom, 18)

            if core.coreMissing {
                Text("请检查 App 安装后重试。")
            } else if editing {
                PairForm(status: core.status, onDone: { showEdit = false })
            } else {
                connectionDetails
                agentActivity.padding(.top, 14)
            }

            Divider().padding(.top, 16).padding(.bottom, 7)
            if core.status.paired && !editing {
                action("编辑连接", icon: "slider.horizontal.3", trailing: "chevron.right") {
                    showEdit = true; armUnpair = false
                }
                if armUnpair {
                    VStack(alignment: .leading, spacing: 9) {
                        Text("取消配对后，下次连接需要新的配对码。")
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                        HStack {
                            Button("保留配对") { armUnpair = false }
                            Spacer()
                            Button("确认取消配对", role: .destructive) {
                                armUnpair = false; core.send("unpair", operation: true)
                            }.tint(.red)
                        }.controlSize(.small)
                    }.padding(10)
                } else {
                    Button { armUnpair = true } label: {
                        Label("取消配对…", systemImage: "link")
                            .font(.system(size: 12))
                    }.buttonStyle(ActionStyle(destructive: true)).disabled(core.busy)
                }
                Divider().padding(.vertical, 7)
            }
            HStack {
                Text("CLI 与 App 实时同步").font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                Button { NSApp.terminate(nil) } label: {
                    Label("退出", systemImage: "power").font(.system(size: 11))
                }
                    .buttonStyle(ActionStyle(compact: true))
                    .help("退出 ego bridge（⌘Q）")
                    .keyboardShortcut("q")
            }.padding(.horizontal, 10).padding(.vertical, 5)
        }
        .padding(20).frame(width: 360)
        .background(Color(nsColor: .windowBackgroundColor))
        .fixedSize(horizontal: false, vertical: true)
        .onChange(of: core.status.paired) { paired in if !paired { showEdit = false; armUnpair = false } }
    }

    private var agentActivity: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label("Agent 活动", systemImage: "cursorarrow.rays")
                    .font(.system(size: 11, weight: .medium))
                Spacer()
                Text(core.status.activeRuns > 0 ? "\(core.status.activeRuns) 个命令执行中" : "桥接命令空闲")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            if core.status.agentSpaces.isEmpty {
                Text(core.status.activityState == "ready" ? "暂无 Agent 空间" :
                        core.status.activityState == "unavailable" ? "暂时无法读取浏览器活动" : "正在读取浏览器活动…")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            } else {
                ForEach(core.status.agentSpaces) { space in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(space.name).lineLimit(1).help(space.name)
                            Spacer(minLength: 4)
                            Text(space.ownership == "agent" ? "Agent 控制" : "你在控制")
                                .font(.system(size: 10)).foregroundStyle(.secondary)
                        }.font(.system(size: 12, weight: .medium))
                        Text(space.recentTitle.isEmpty ? "还没有页面标题" : "最近页面 · \(space.recentTitle)")
                            .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                            .help(space.recentTitle)
                    }
                }
                if core.status.agentSpaceTotal > core.status.agentSpaces.count {
                    Text("另有 \(core.status.agentSpaceTotal - core.status.agentSpaces.count) 个 Agent 空间")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var connectionDetails: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "desktopcomputer").foregroundStyle(.secondary).frame(width: 18)
                VStack(alignment: .leading, spacing: 3) {
                    Text("远端设备").font(.system(size: 11)).foregroundStyle(.secondary)
                    Text(core.status.host ?? "—").font(.system(size: 13, weight: .medium))
                        .textSelection(.enabled).lineLimit(2)
                        .help(core.status.host ?? "")
                }
                Spacer(minLength: 8)
                Text(String(core.status.port ?? 3325)).font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Divider().padding(.vertical, 12)
            HStack(spacing: 10) {
                Image(systemName: "globe").foregroundStyle(.secondary).frame(width: 18)
                Text("ego-lite").font(.system(size: 12, weight: .medium))
                Spacer()
                Text(core.status.state != "connected" ? "连接后检测" :
                        core.status.egoRunning == true ? "运行中" : "按需启动")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
    }

    private func action(_ name: String, icon: String, trailing: String, perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            HStack(spacing: 9) {
                Image(systemName: icon).frame(width: 16)
                Text(name)
                Spacer()
                Image(systemName: trailing).font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
            }.font(.system(size: 12))
        }.buttonStyle(ActionStyle()).disabled(core.busy)
    }
}

struct PairForm: View {
    @EnvironmentObject var core: CoreProcess
    @State private var host = ""
    @State private var port = "3325"
    @State private var code = ""
    var onDone: () -> Void
    init(status: CoreStatus, onDone: @escaping () -> Void) {
        _host = State(initialValue: status.host ?? UserDefaults.standard.string(forKey: "egoBridgeHost") ?? "")
        _port = State(initialValue: String(status.port ?? 3325))
        self.onDone = onDone
    }
    private var valid: Bool {
        !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        (Int(port).map { 1...65535 ~= $0 } ?? false) &&
        (core.status.paired || !code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("主机名或 IP").font(.system(size: 11)).foregroundStyle(.secondary)
                    TextField("devbox.internal", text: $host)
                        .accessibilityLabel("主机名或 IP")
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text("端口").font(.system(size: 11)).foregroundStyle(.secondary)
                    TextField("3325", text: $port).accessibilityLabel("端口")
                }.frame(width: 65)
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(core.status.paired ? "新配对码（可选）" : "配对码")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                SecureField("xxxx-xxxx", text: $code).accessibilityLabel("配对码")
                Text(core.status.paired ? "留空保留当前配对，仅更新设备地址。" : "在 devbox 运行 ego-browser pair 获取，5 分钟内有效。")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error = core.status.operationError {
                Label(error, systemImage: "exclamationmark.circle")
                    .font(.system(size: 11)).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if core.status.paired { Button("取消") { onDone() }.disabled(core.busy) }
                Spacer()
                if core.busy { ProgressView().controlSize(.small) }
                Button(core.busy ? "正在保存…" : core.status.paired ? "保存连接" : "配对并连接") {
                    let cleanCode = code.trimmingCharacters(in: .whitespacesAndNewlines)
                    let command = cleanCode.isEmpty ? "configure" : "pair"
                    core.send(command, extra: ["host": host.trimmingCharacters(in: .whitespacesAndNewlines),
                                              "port": Int(port) ?? 3325, "code": cleanCode], operation: true)
                    code = ""
                }
                .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                .disabled(!valid || core.busy)
            }.padding(.top, 3)
        }
        .textFieldStyle(.roundedBorder).controlSize(.regular)
        .onChange(of: core.operationSucceeded) { _ in onDone() }
    }
}
