package ru.leshy.satellite

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Local identity preparation, NOT an authenticated pairing session.
 * A private signing key stays inside the Android Keystore; only its X.509
 * public key and SHA-256 fingerprint are made available for future enrollment.
 *
 * A future server pairing API must verify proof of possession, an expiring
 * one-time owner-approved challenge, device permissions and revocation.
 * Never substitute LESHY_API_TOKEN or owner privileges for that protocol.
 */
internal object DeviceIdentity {
    private const val KEY_ALIAS = "leshy_satellite_device_signing_v1"
    private const val SIGNING_CONTEXT = "leshy-satellite-pair-v1\u0000"

    data class PublicIdentity(
        val publicKeyX509Base64Url: String,
        val fingerprintSha256: String
    )

    private fun store(): KeyStore =
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    @Synchronized
    fun existing(): PublicIdentity? {
        val certificate = store().getCertificate(KEY_ALIAS) ?: return null
        val encoded = certificate.publicKey.encoded
            ?: throw IllegalStateException("No public key encoding")
        val fingerprint = MessageDigest.getInstance("SHA-256")
            .digest(encoded)
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        val publicKey = Base64.encodeToString(
            encoded, Base64.NO_WRAP or Base64.NO_PADDING or Base64.URL_SAFE
        )
        return PublicIdentity(publicKey, fingerprint)
    }

    @Synchronized
    fun createIfMissing(): PublicIdentity {
        existing()?.let { return it }
        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore"
        )
        generator.initialize(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .build()
        )
        generator.generateKeyPair()
        return existing() ?: throw IllegalStateException("Device identity unavailable")
    }

    /**
     * Reserved for a server-issued 32-byte random challenge after an explicit
     * owner-authorized pairing request. Does not send the signature anywhere.
     */
    @Synchronized
    fun signPairingChallenge(challenge: ByteArray): ByteArray {
        require(challenge.size == 32) { "Pairing challenge must be 32 bytes" }
        val privateKey = store().getKey(KEY_ALIAS, null) as? PrivateKey
            ?: throw IllegalStateException("Create identity before pairing")
        val signature = Signature.getInstance("SHA256withECDSA")
        signature.initSign(privateKey)
        signature.update(SIGNING_CONTEXT.toByteArray(StandardCharsets.US_ASCII))
        signature.update(challenge)
        return signature.sign()
    }

    /**
     * Deletes the device's local key permanently. When server pairing exists,
     * the server credential must ALSO be revoked through an authorized flow.
     */
    @Synchronized
    fun deleteLocalKey() {
        val keyStore = store()
        if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
    }
}
