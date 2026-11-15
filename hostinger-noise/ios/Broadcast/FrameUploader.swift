import Foundation

/// Sends screen frames (a JPEG or OCR text) to the server on a queue of its own, apart from the audio.
///
/// It holds at most one frame in flight and one waiting. A new frame replaces the waiting one, so a slow
/// network drops old frames instead of piling them up: the server wants the screen as it is now. Same
/// retry rule as the audio: a short timeout and one retry on a new connection.
final class FrameUploader: @unchecked Sendable {
    struct Item {
        let seq: Int
        let path: String
        let query: [URLQueryItem]
        let body: Data
        let contentType: String
        var attempts = 0
    }

    struct Stats {
        var sent = 0
        var bytes = 0
        var failed = 0
        var retries = 0
        var dropped = 0
        var lastMs = 0
        var busy = false
        var waiting = false
    }

    var log: ((String) -> Void)?
    /// The server took the frame but did not read it (it was busy with the frame before). The caller must send the
    /// same screen again: an unchanged screen is never sent twice, so a skipped first frame of another app would
    /// keep the session from pausing.
    var onNotRead: (() -> Void)?

    private let queue = DispatchQueue(label: "lens.broadcast.frames")
    private let urlSession: URLSession
    private let maxAttempts: Int
    private var inFlight: Item?
    private var waiting: Item?
    private var counters = Stats()

    init(requestTimeout: TimeInterval = 8, maxAttempts: Int = 2) {
        self.maxAttempts = maxAttempts
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = requestTimeout
        config.timeoutIntervalForResource = requestTimeout * 2
        config.waitsForConnectivity = false
        config.httpMaximumConnectionsPerHost = 1
        config.urlCache = nil
        urlSession = URLSession(configuration: config)
    }

    var stats: Stats {
        queue.sync {
            var s = counters
            s.busy = inFlight != nil
            s.waiting = waiting != nil
            return s
        }
    }

    /// True when a frame is in flight and another waits: the caller can skip making a new one.
    var isFull: Bool { queue.sync { inFlight != nil && waiting != nil } }

    func offer(_ item: Item) {
        queue.async {
            if self.waiting != nil { self.counters.dropped += 1 }
            self.waiting = item
            self.pump()
        }
    }

    private func pump() {
        guard inFlight == nil, var item = waiting else { return }
        waiting = nil
        item.attempts += 1
        inFlight = item
        var components = URLComponents(url: LensConfig.serverURL.appendingPathComponent(item.path), resolvingAgainstBaseURL: false)!
        components.queryItems = item.query + [URLQueryItem(name: "session", value: LensConfig.session), URLQueryItem(name: "seq", value: String(item.seq))]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue(item.contentType, forHTTPHeaderField: "Content-Type")
        let started = Date()
        urlSession.uploadTask(with: request, from: item.body) { data, response, error in
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let ok = error == nil && code == 200
            if ok, let data, let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any], reply["reading"] as? Bool == false,
               let reason = reply["reason"] as? String, reason == "busy" || reason == "queue full" {
                self.onNotRead?()
            }
            self.queue.async {
                self.counters.lastMs = Int(Date().timeIntervalSince(started) * 1000)
                self.inFlight = nil
                if ok {
                    self.counters.sent += 1
                    self.counters.bytes += item.body.count
                } else if item.attempts < self.maxAttempts && self.waiting == nil {
                    // Retry only when no newer frame waits; a newer frame is worth more than an old one.
                    self.counters.retries += 1
                    self.waiting = item
                } else {
                    self.counters.failed += 1
                    self.log?("frame POST \(item.seq) failed: \(error?.localizedDescription ?? "HTTP \(code)")")
                }
                self.pump()
            }
        }.resume()
    }
}
