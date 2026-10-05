// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "StudioDistribution",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "Distribution", targets: ["Distribution"]),
        .library(name: "DirectDistribution", targets: ["DirectDistribution"]),
    ],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0"),
    ],
    targets: [
        .target(name: "Distribution"),
        .target(name: "DirectDistribution", dependencies: ["Distribution", .product(name: "Sparkle", package: "Sparkle")]),
        .testTarget(name: "DistributionTests", dependencies: ["Distribution", "DirectDistribution"]),
    ]
)
