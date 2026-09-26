import CryptoKit
import Foundation

enum FixtureSigningError: Error { case arguments }

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count == 2 || arguments.count == 3 else { throw FixtureSigningError.arguments }
let keyURL = URL(fileURLWithPath: arguments[1])
let result: [String: String]
if arguments[0] == "generate", arguments.count == 2 {
    let key = Curve25519.Signing.PrivateKey()
    try key.rawRepresentation.write(to: keyURL, options: .withoutOverwriting)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: keyURL.path)
    result = ["public_key": key.publicKey.rawRepresentation.base64EncodedString()]
} else if arguments[0] == "sign", arguments.count == 3 {
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(contentsOf: keyURL))
    let bytes = try Data(contentsOf: URL(fileURLWithPath: arguments[2]), options: .mappedIfSafe)
    result = ["signature": try key.signature(for: bytes).base64EncodedString()]
} else {
    throw FixtureSigningError.arguments
}
let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data("\n".utf8))
