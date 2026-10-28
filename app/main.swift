// Local Audiobook.app: a native window around web/index.html.
// Starts `python3 server.py` as a hidden child process on launch and stops it on quit,
// so the human opens the app from Spotlight and never deals with a server or a URL.
// Built by app/build.sh, which writes the repo path and python path into Info.plist.
import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var log = ""
    var quitting = false
    var restarts: [Date] = []

    func info(_ key: String) -> String { Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "" }

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()   // keeps localStorage (chosen voice) between launches
        cfg.mediaTypesRequiringUserActionForPlayback = []   // the next sentence starts on its own when its audio is ready
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 880),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Local Audiobook"
        window.backgroundColor = NSColor.black
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("LocalAudiobookMain")
        window.makeKeyAndOrderFront(nil)
        web.loadHTMLString(splash("Starting…"), baseURL: nil)
        NSApp.activate(ignoringOtherApps: true)
        startServer()
    }

    func startServer() {
        let repo = info("AudiobookRepo"), py = info("AudiobookPython")
        let script = repo + "/server.py"
        guard FileManager.default.fileExists(atPath: script) else {
            web.loadHTMLString(splash("Can't find \(script).<br>Rebuild the app with app/build.sh."), baseURL: nil); return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = [script, "--no-open", "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = info("AudiobookPath")   // apps launched from Finder get a bare PATH
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        var loaded = false
        log = ""
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            guard let self, let s = String(data: h.availableData, encoding: .utf8), !s.isEmpty else { return }
            DispatchQueue.main.async {
                self.log += s
                if !loaded, let r = self.log.range(of: #"http://127\.0\.0\.1:\d+/"#, options: .regularExpression),
                   let url = URL(string: String(self.log[r])) {
                    loaded = true
                    // same address as the open page (restart): the page reconnects itself, keeping unsaved edits
                    if self.web.url?.port != url.port || self.web.url?.host != url.host { self.web.load(URLRequest(url: url)) }
                }
            }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                // backend died while the app is open (crash, killed): restart it, unless it keeps dying
                self.restarts = self.restarts.filter { $0.timeIntervalSinceNow > -60 } + [Date()]
                if self.restarts.count <= 5 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.startServer() }
                    return
                }
                self.web.loadHTMLString(self.splash("Backend exited (\(proc.terminationStatus)).<pre>\(self.log.replacingOccurrences(of: "<", with: "&lt;"))</pre>"), baseURL: nil)
            }
        }
        do { try p.run(); server = p }
        catch { web.loadHTMLString(splash("Can't start \(py): \(error.localizedDescription)"), baseURL: nil) }
    }

    func splash(_ msg: String) -> String {
        """
        <html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#000;color:#e8e2d0;
        font:14px -apple-system,sans-serif;text-align:center"><div><div style="font-size:42px;color:#e8c46a">📖</div>
        <div style="color:#e8c46a;font-weight:600;letter-spacing:.1em">LOCAL AUDIOBOOK</div><p>\(msg)</p></div></body></html>
        """
    }

    func applicationWillTerminate(_ note: Notification) { quitting = true; server?.terminate() }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // confirm()/alert() from the page ("Delete this audio?") as native sheets
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler done: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { done($0 == .alertFirstButtonReturn) }
    }
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler done: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.beginSheetModal(for: window) { _ in done() }
    }
    // external links (target=_blank or other hosts) open in the default browser
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for action: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = action.request.url { NSWorkspace.shared.open(u) }
        return nil
    }
    func webView(_ w: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = action.request.url, action.navigationType == .linkActivated, u.host != "127.0.0.1" {
            NSWorkspace.shared.open(u); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    @objc func reload() { web.reload() }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Hide Local Audiobook", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Local Audiobook", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")   // without this menu, ⌘C/⌘V/⌘Z don't reach the editor
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        view.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        view.items.last?.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = view
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
