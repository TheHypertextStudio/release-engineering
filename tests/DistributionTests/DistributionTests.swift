import Foundation
import XCTest
@testable import Distribution

final class DistributionTests: XCTestCase {
    func testBuildInformationReadsExactArchiveValues() throws {
        let info = try BuildInformation(info: ["CFBundleIdentifier": "dev.williecubed.app", "CFBundleShortVersionString": "1.2.3", "CFBundleVersion": "42"], channel: .appStore)
        XCTAssertEqual(info.version, "1.2.3")
        XCTAssertEqual(info.buildNumber, "42")
        XCTAssertEqual(info.bundleIdentifier, "dev.williecubed.app")
        XCTAssertEqual(info.channel, .appStore)
    }

    func testMissingBuildInformationFails() {
        XCTAssertThrowsError(try BuildInformation(info: [:], channel: .direct))
    }

    func testUpdatesRequireHTTPSAndRealPublicKey() throws {
        let key = Data(repeating: 7, count: 32).base64EncodedString()
        XCTAssertThrowsError(try UpdateConfiguration(feedURL: URL(string: "http://example.com/feed.xml")!, publicKey: key))
        XCTAssertThrowsError(try UpdateConfiguration(feedURL: URL(string: "https://example.com/feed.xml")!, publicKey: "missing"))
        let config = try UpdateConfiguration(info: ["SUFeedURL": "https://example.com/feed.xml", "SUPublicEDKey": key])
        XCTAssertEqual(config.feedURL.absoluteString, "https://example.com/feed.xml")
        XCTAssertEqual(config.publicKey, key)
    }

    func testMissingUpdateConfigurationFails() {
        XCTAssertThrowsError(try UpdateConfiguration(info: [:]))
    }
}
