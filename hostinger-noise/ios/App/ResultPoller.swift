import Foundation

/// Polls the server each second for new transcripts and the Live Activity state.
/// Polling, not SSE: it is simple and recovers by itself.
@MainActor
final class ResultPoller {
    private var timer: Timer?
    private var since = 0
    private var primed = false
    private var inFlight = false
    private(set) var polls = 0
    private(set) var failures = 0
    var onResponse: ((LatestResponse, [TranscriptResult], [CheckResult]) -> Void)?
    var onFailure: (() -> Void)?

    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 4
        config.timeoutIntervalForResource = 6
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    func start() {
        guard timer == nil else { return }
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        poll()
    }

    /// A new take: the next poll replays nothing as new. When the server counts ids from 1 again, poll() starts over.
    func reset() {
        primed = false
    }

    func poll() {
        guard !inFlight else { return }
        inFlight = true
        let url = LensAPI.latestURL(since: since, source: "app")
        Task {
            defer { self.inFlight = false }
            do {
                let (data, _) = try await session.data(from: url)
                let response = try LensAPI.decode(data)
                self.polls += 1
                let isFirst = !self.primed
                self.primed = true
                // A restarted server counts ids from 1 again: start over, or no new result would ever arrive.
                if response.lastItemId == nil, (response.latestId ?? 0) < self.since { self.since = 0 }
                if let last = response.lastItemId { self.since = max(self.since, last) }
                // On the first poll, do not replay old results as new ones.
                self.onResponse?(response, isFirst ? [] : response.results, isFirst ? [] : response.checks)
            } catch {
                self.failures += 1
                self.onFailure?()
                if self.failures % 10 == 1 { log("poll error: \(error.localizedDescription)") }
            }
        }
    }
}
