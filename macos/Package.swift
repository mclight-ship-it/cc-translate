// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CCTranslateMac",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "CCTranslateMac", targets: ["CCTranslateMac"]),
        .library(name: "CCProcessSupport", type: .dynamic, targets: ["CCProcessSupport"]),
        .library(name: "CCTranslateSupport", targets: ["CCTranslateSupport"])
    ],
    targets: [
        .target(name: "CCProcessSupport"),
        .target(name: "CCTranslateSupport", dependencies: ["CCProcessSupport"]),
        .executableTarget(name: "CCTranslateMac", dependencies: ["CCTranslateSupport"]),
        .testTarget(name: "CCTranslateSupportTests", dependencies: ["CCTranslateSupport"])
    ],
    swiftLanguageVersions: [.v5]
)
