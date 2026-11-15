import Foundation
import Observation

/// In-app log. Each line also goes to the server (/api/log) in batches.
@MainActor
@Observable
final class Log {
    static let shared = Log()

    private(set) var lines: [String] = []
    @ObservationIgnored private var unsent: [String] = []
    @ObservationIgnored private let clock: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    func add(_ line: String) {
        let stamped = "\(clock.string(from: Date())) \(line)"
        NSLog("[lens] %@", line)
        lines.append(stamped)
        if lines.count > 300 { lines.removeFirst(lines.count - 300) }
        unsent.append(line)
        if unsent.count > 200 { unsent.removeFirst(unsent.count - 200) }
    }

    func flush() {
        guard !unsent.isEmpty else { return }
        let batch = unsent
        unsent.removeAll()
        ServerPost.json("api/log", ["source": "app", "session": LensConfig.session, "lines": batch])
    }
}

@MainActor
func log(_ line: String) {
    Log.shared.add(line)
}
