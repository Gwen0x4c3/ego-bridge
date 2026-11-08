// Compile with -D PREVIEW alongside EgoBridgeApp.swift; offscreen native rendering.
import AppKit
import SwiftUI

@main
struct PreviewPanels {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let dir = CommandLine.arguments[1]
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        // Render the actual menu-bar drawing at 2×, alongside its state labels.
        let icons = NSImage(size: NSSize(width: 360, height: 66), flipped: false) { _ in
            NSColor.white.setFill(); NSRect(x: 0, y: 0, width: 360, height: 66).fill()
            for (index, state) in ["connected", "connecting", "error", "off"].enumerated() {
                let x = CGFloat(index * 90 + 34)
                BridgeIcon.make(state: state).draw(in: NSRect(x: x, y: 31, width: 22, height: 18))
                let label = ["已连接", "连接中", "需要处理", "已暂停"][index]
                let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 10), .foregroundColor: NSColor.black]
                let text = NSAttributedString(string: label, attributes: attributes)
                text.draw(at: NSPoint(x: CGFloat(index * 90 + 45) - text.size().width / 2, y: 10))
            }
            return true
        }
        if let data = icons.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data),
           let png = bitmap.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: dir + "/tray-icons.png"))
        }
        let states: [(String, CoreStatus, Bool)] = [
            ("connected", CoreStatus(state: "connected", detail: "已连接", paired: true,
                enabled: true, host: "devbox.example", port: 3325, egoRunning: true,
                activityState: "ready"), false),
            ("reconnecting", CoreStatus(state: "connecting", detail: "连接断开，正在重连…", paired: true,
                enabled: true, host: "devbox.example", port: 3325), false),
            ("edit", CoreStatus(state: "connected", detail: "已连接", paired: true,
                enabled: true, host: "devbox.example", port: 3325, egoRunning: true), true),
            ("unpaired", CoreStatus(state: "off", detail: "未配对"), false),
            ("paused", CoreStatus(state: "off", detail: "已关闭", paired: true,
                host: "devbox-with-a-long-hostname.example.internal", port: 3325), false),
            ("pair-error", CoreStatus(state: "off", detail: "未配对",
                operationError: "配对失败：配对码无效、过期或已使用，请在 devbox 重新获取。"), false),
            ("connected-dark", CoreStatus(state: "connected", detail: "已连接", paired: true,
                enabled: true, host: "devbox.example", port: 3325, egoRunning: true,
                activityState: "ready"), false),
            ("agent-active", CoreStatus(state: "connected", detail: "已连接", paired: true,
                enabled: true, host: "devbox.example", port: 3325, egoRunning: true,
                activeRuns: 1, activityState: "ready",
                agentSpaces: [AgentSpace(id: 7, name: "验证页面与截图", ownership: "agent",
                                        recentTitle: "Example Domain")], agentSpaceTotal: 1), false)
        ]
        var index = 0
        func renderNext() {
            if index >= states.count { exit(0) }
            let (name, status, editing) = states[index]
            CoreProcess.shared.status = status
            let root = PanelView(initialEdit: editing)
                .environmentObject(CoreProcess.shared)
                .environment(\.colorScheme, name.hasSuffix("-dark") ? .dark : .light)
            let view = NSHostingView(rootView: root)
            let window = NSWindow(contentRect: NSRect(x: -2000, y: -2000, width: 360, height: 600),
                                  styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = view
            window.isReleasedWhenClosed = false
            view.frame = NSRect(x: 0, y: 0, width: 360, height: 600)
            view.layoutSubtreeIfNeeded()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                let height = ceil(view.fittingSize.height)
                view.setFrameSize(NSSize(width: 360, height: height))
                window.setContentSize(NSSize(width: 360, height: height))
                view.layoutSubtreeIfNeeded()
                guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { exit(2) }
                view.cacheDisplay(in: view.bounds, to: bitmap)
                guard let png = bitmap.representation(using: .png, properties: [:]) else { exit(3) }
                try! png.write(to: URL(fileURLWithPath: dir + "/" + name + ".png"))
                print("\(name): 360 × \(height)")
                window.close()
                index += 1
                DispatchQueue.main.async { renderNext() }
            }
        }
        DispatchQueue.main.async { renderNext() }
        app.run()
    }
}
