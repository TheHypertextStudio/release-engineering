import Foundation

public enum DistributionChannel: String, Codable, Sendable {
    case direct
    case appStore
    case development

    public var supportsInAppUpdates: Bool { self == .direct }
}

public enum DistributionError: Error, Equatable {
    case invalidBuildInformation
    case invalidFeedURL
    case invalidPublicKey
    case mismatchedBundleConfiguration
}

public struct BuildInformation: Sendable, Equatable {
    public let version: String
    public let buildNumber: String
    public let bundleIdentifier: String
    public let channel: DistributionChannel

    public init(info: [String: Any], channel: DistributionChannel) throws {
        guard let version = info["CFBundleShortVersionString"] as? String,
              version.range(of: #"^\d+\.\d+\.\d+$"#, options: .regularExpression) != nil,
              let buildNumber = info["CFBundleVersion"] as? String,
              let build = Int(buildNumber), build > 0,
              let bundleIdentifier = info["CFBundleIdentifier"] as? String,
              !bundleIdentifier.isEmpty else {
            throw DistributionError.invalidBuildInformation
        }
        self.version = version
        self.buildNumber = buildNumber
        self.bundleIdentifier = bundleIdentifier
        self.channel = channel
    }

    public init(bundle: Bundle = .main, channel: DistributionChannel) throws {
        try self.init(info: bundle.infoDictionary ?? [:], channel: channel)
    }
}

public struct UpdateConfiguration: Sendable, Equatable {
    public let feedURL: URL
    public let publicKey: String

    public init(feedURL: URL, publicKey: String) throws {
        guard feedURL.scheme == "https", let host = feedURL.host, !host.isEmpty,
              feedURL.user == nil, feedURL.password == nil, feedURL.fragment == nil else {
            throw DistributionError.invalidFeedURL
        }
        guard let key = Data(base64Encoded: publicKey), key.count == 32 else {
            throw DistributionError.invalidPublicKey
        }
        self.feedURL = feedURL
        self.publicKey = publicKey
    }

    public init(info: [String: Any]) throws {
        guard let feed = info["SUFeedURL"] as? String, let url = URL(string: feed) else {
            throw DistributionError.invalidFeedURL
        }
        guard let key = info["SUPublicEDKey"] as? String else {
            throw DistributionError.invalidPublicKey
        }
        try self.init(feedURL: url, publicKey: key)
    }
}
