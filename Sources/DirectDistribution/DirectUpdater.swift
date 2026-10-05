import Combine
import Distribution
import Foundation
import Sparkle
import SwiftUI

@MainActor
public final class DirectUpdater: ObservableObject {
    public let configuration: UpdateConfiguration
    public let buildInformation: BuildInformation
    @Published public private(set) var canCheckForUpdates = false
    private let updater: SPUUpdater
    private var observation: AnyCancellable?

    public convenience init(bundle: Bundle = .main) throws {
        let configuration = try UpdateConfiguration(info: bundle.infoDictionary ?? [:])
        try self.init(configuration: configuration, bundle: bundle)
    }

    public init(configuration: UpdateConfiguration, bundle: Bundle = .main) throws {
        let embedded = try UpdateConfiguration(info: bundle.infoDictionary ?? [:])
        guard embedded == configuration else {
            throw DistributionError.mismatchedBundleConfiguration
        }
        self.configuration = configuration
        buildInformation = try BuildInformation(bundle: bundle, channel: .direct)
        let driver = SPUStandardUserDriver(hostBundle: bundle, delegate: nil)
        updater = SPUUpdater(hostBundle: bundle, applicationBundle: bundle, userDriver: driver, delegate: nil)
        observation = updater.publisher(for: \.canCheckForUpdates)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] value in
                MainActor.assumeIsolated { self?.canCheckForUpdates = value }
            }
    }

    public func start() throws {
        try updater.start()
    }

    public func checkForUpdates() {
        if canCheckForUpdates { updater.checkForUpdates() }
    }
}

@MainActor
public struct DistributionCommands: Commands {
    @ObservedObject private var updater: DirectUpdater

    public init(updater: DirectUpdater) {
        self.updater = updater
    }

    public var body: some Commands {
        CommandGroup(after: .appInfo) {
            Button("Check for Updates…") { updater.checkForUpdates() }
                .disabled(!updater.canCheckForUpdates)
        }
    }
}
