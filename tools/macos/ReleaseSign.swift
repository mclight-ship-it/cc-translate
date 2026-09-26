import CryptoKit
import Foundation

enum ReleaseSigningError: Error { case arguments, key, verification }

let args = Array(CommandLine.arguments.dropFirst())
guard args.count == 3 || args.count == 4,
      let publicBytes = Data(base64Encoded: args[1]), publicBytes.count == 32 else {
    throw ReleaseSigningError.arguments
}
let publicKey = try Curve25519.Signing.PublicKey(rawRepresentation: publicBytes)
let payload = try Data(contentsOf: URL(fileURLWithPath: args[2]), options: .mappedIfSafe)
if args[0] == "sign", args.count == 3 {
    // The CI secret is neither an argument nor a file and is never echoed.
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let encoded = String(data: input, encoding: .utf8),
          let seed = Data(base64Encoded: encoded.trimmingCharacters(in: .whitespacesAndNewlines)),
          seed.count == 32 else { throw ReleaseSigningError.key }
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: seed)
    guard key.publicKey.rawRepresentation == publicBytes else { throw ReleaseSigningError.key }
    let signature = try key.signature(for: payload)
    guard publicKey.isValidSignature(signature, for: payload) else { throw ReleaseSigningError.verification }
    print(signature.base64EncodedString())
} else if args[0] == "verify", args.count == 4 {
    guard let signature = Data(base64Encoded: args[3]), signature.count == 64,
          publicKey.isValidSignature(signature, for: payload) else { throw ReleaseSigningError.verification }
    print("verified")
} else {
    throw ReleaseSigningError.arguments
}
