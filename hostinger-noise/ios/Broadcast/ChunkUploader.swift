import Foundation

/// Sends audio chunks to the server one at a time, in order, with retries.
///
/// The first device runs sent each chunk at once on the shared session, up to 3 at a time on one HTTP/2
/// connection with a 15 s idle timeout. When that connection stalled after the broadcast started, all 3
/// uploads and the heartbeats behind them hung together, failed together after the timeout, and the
/// chunks made in the meantime were dropped. Now: a session of its own for audio, one upload at a time,
/// a short timeout so a stalled connection is given up fast, a retry on a new connection, and a small
/// queue that keeps the newest chunks when the network is slow. Mu-law halves the bytes.
final class ChunkUploader: @unchecked Sendable {
    struct Chunk {
        let seq: Int
        let mulaw: Data
        let seconds: Double
        var attempts = 0
    }

    struct Stats {
        var sent = 0
        var bytes = 0
        var failed = 0
        var retries = 0
        var dropped = 0
        var queued = 0
        var busy = false
        var lastMs = 0
    }

    var log: ((String) -> Void)?

    private let baseURL: URL
    private let session: String
    private let maxQueue: Int
    private let maxAttempts: Int
    private let queue = DispatchQueue(label: "lens.broadcast.upload")
    private var pending: [Chunk] = []
    private var busy = false
    private var counters = Stats()
    private let urlSession: URLSession

    init(baseURL: URL = LensConfig.serverURL, session: String = LensConfig.session, maxQueue: Int = 6, maxAttempts: Int = 3,
         requestTimeout: TimeInterval = 8) {
        self.baseURL = baseURL
        self.session = session
        self.maxQueue = maxQueue
        self.maxAttempts = maxAttempts
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = requestTimeout
        config.timeoutIntervalForResource = requestTimeout * 2.5
        config.waitsForConnectivity = false
        config.httpMaximumConnectionsPerHost = 1
        config.urlCache = nil
        urlSession = URLSession(configuration: config)
    }

    var stats: Stats {
        queue.sync {
            var s = counters
            s.queued = pending.count
            s.busy = busy
            return s
        }
    }

    func enqueue(_ chunk: Chunk) {
        queue.async {
            if self.pending.count >= self.maxQueue {
                // Keep the newest audio: the Island must show what is said now.
                let old = self.pending.remove(at: self.busy ? 1 : 0)
                self.counters.dropped += 1
                self.log?("upload queue full, dropped chunk \(old.seq)")
            }
            self.pending.append(chunk)
            self.pump()
        }
    }

    /// Waits until the queue is empty (for tests and for the end of a broadcast).
    func drain(timeout: TimeInterval) -> Bool {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if queue.sync(execute: { pending.isEmpty && !busy }) { return true }
            Thread.sleep(forTimeInterval: 0.05)
        }
        return false
    }

    private func pump() {
        guard !busy, var chunk = pending.first else { return }
        busy = true
        chunk.attempts += 1
        pending[0] = chunk
        var components = URLComponents(url: baseURL.appendingPathComponent("api/audio"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "session", value: session),
            URLQueryItem(name: "seq", value: String(chunk.seq)),
            URLQueryItem(name: "rate", value: "16000"),
            URLQueryItem(name: "encoding", value: "mulaw"),
            URLQueryItem(name: "attempt", value: String(chunk.attempts)),
        ]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let started = Date()
        let attempt = chunk.attempts
        let seq = chunk.seq
        let size = chunk.mulaw.count
        urlSession.uploadTask(with: request, from: chunk.mulaw) { _, response, error in
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let ok = error == nil && code == 200
            self.queue.async {
                self.busy = false
                self.counters.lastMs = Int(Date().timeIntervalSince(started) * 1000)
                guard self.pending.first?.seq == seq else { self.pump(); return }
                if ok {
                    self.pending.removeFirst()
                    self.counters.sent += 1
                    self.counters.bytes += size
                    self.pump()
                    return
                }
                let reason = error?.localizedDescription ?? "HTTP \(code)"
                if attempt >= self.maxAttempts {
                    self.pending.removeFirst()
                    self.counters.failed += 1
                    self.log?("audio POST failed for chunk \(seq) after \(attempt) attempts: \(reason)")
                    self.pump()
                } else {
                    self.counters.retries += 1
                    self.log?("audio POST attempt \(attempt) for chunk \(seq) failed (\(reason)), retrying")
                    self.queue.asyncAfter(deadline: .now() + 0.5) { self.pump() }
                }
            }
        }.resume()
    }
}

/// G.711 mu-law: 8 bits per sample instead of 16. The server decodes it (`ULAW` in server.py).
enum MuLaw {
    static func encode(_ samples: [Int16]) -> Data {
        var out = Data(count: samples.count)
        out.withUnsafeMutableBytes { raw in
            let bytes = raw.bindMemory(to: UInt8.self)
            for (i, s) in samples.enumerated() { bytes[i] = encode(s) }
        }
        return out
    }

    static func encode(_ value: Int16) -> UInt8 {
        let bias = 0x84
        let clip = 32_635
        var sample = Int(value)
        let sign = sample < 0 ? 0x80 : 0
        if sign != 0 { sample = -sample }
        sample = min(sample, clip) + bias
        var exponent = 7
        var mask = 0x4000
        while exponent > 0 && sample & mask == 0 {
            exponent -= 1
            mask >>= 1
        }
        let mantissa = (sample >> (exponent + 3)) & 0x0F
        return UInt8(~(sign | (exponent << 4) | mantissa) & 0xFF)
    }
}
