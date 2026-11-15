import Foundation
import Speech

/// The on-device speech models for the broadcast extension. The extension only checks that a model is installed; the
/// app downloads it here (Debug > Speech), because a download takes 20 to 30 s and the extension must not wait.
enum SpeechModels {
    /// One line per language, such as "en: installed (SpeechTranscriber)".
    static func status() async -> String {
        guard #available(iOS 26.0, *) else { return "On-device speech needs iOS 26." }
        var lines: [String] = []
        for lang in ["en", "uk", "ru"] {
            let module = lang == "en" ? "SpeechTranscriber" : "DictationTranscriber"
            lines.append("\(lang): \(await OnDeviceSpeechSupport.installed(lang) ? "installed" : "not installed") (\(module))")
        }
        return lines.joined(separator: "\n")
    }

    /// Downloads the models for English, Ukrainian and Russian. Returns the new status.
    static func install() async -> String {
        guard #available(iOS 26.0, *) else { return "On-device speech needs iOS 26." }
        var errors: [String] = []
        for lang in ["en", "uk", "ru"] {
            let locale = OnDeviceSpeechSupport.locale(for: lang)
            // The same modules as the extension, so the same assets.
            let module: any SpeechModule = lang == "en"
                ? SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults], attributeOptions: [.audioTimeRange])
                : DictationTranscriber(locale: locale, contentHints: [], transcriptionOptions: [.punctuation], reportingOptions: [.volatileResults],
                                       attributeOptions: [.audioTimeRange])
            do {
                if let request = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
                    try await request.downloadAndInstall()
                }
            } catch {
                errors.append("\(lang): \(error.localizedDescription)")
            }
        }
        let status = await status()
        return errors.isEmpty ? status : status + "\n" + errors.joined(separator: "\n")
    }
}

/// The locale table and the install check, the same as Broadcast/OnDeviceSpeech.swift (the app does not compile it).
@available(iOS 26.0, *)
enum OnDeviceSpeechSupport {
    static func locale(for lang: String) -> Locale {
        switch lang {
        case "uk": return Locale(identifier: "uk_UA")
        case "ru": return Locale(identifier: "ru_RU")
        default: return Locale(identifier: "en_US")
        }
    }

    static func installed(_ lang: String) async -> Bool {
        let id = locale(for: lang).identifier(.bcp47)
        let locales = lang == "en" ? await SpeechTranscriber.installedLocales : await DictationTranscriber.installedLocales
        return locales.contains { $0.identifier(.bcp47) == id }
    }
}
