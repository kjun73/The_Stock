package com.example.thestock.util

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import com.example.thestock.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

data class AppUpdateInfo(
    val versionCode: Long = 0,
    val versionName: String = "",
    val apkUrl: String = "",
    val notes: String = "",
    val isForce: Boolean = false
)

object UpdateManager {
    private const val TAG = "UpdateManager"
    private const val FIREBASE_RTDB_VERSION_URL =
        "https://thestock-513c0-default-rtdb.asia-southeast1.firebasedatabase.app/app_version.json"

    /**
     * 최신 버전 정보를 확인합니다.
     * 1차: 로컬 백엔드 API (설정된 백엔드 URL/api/version)
     * 2차: Firebase RTDB (외부망/LTE 환경 및 백엔드 미가동 시 전천후 확인)
     */
    suspend fun checkUpdate(backendBaseUrl: String = ""): AppUpdateInfo? = withContext(Dispatchers.IO) {
        // 1. 로컬 백엔드 확인 시도
        if (backendBaseUrl.isNotBlank()) {
            val localInfo = checkVersionFromUrl("${backendBaseUrl.trimEnd('/')}/api/version")
            if (localInfo != null) return@withContext localInfo
        }

        // 2. Firebase RTDB 확인 시도
        return@withContext checkVersionFromUrl(FIREBASE_RTDB_VERSION_URL)
    }

    private fun checkVersionFromUrl(endpointUrl: String): AppUpdateInfo? {
        return runCatching {
            val url = URL(endpointUrl)
            val connection = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 6000
                readTimeout = 10000
                requestMethod = "GET"
                setRequestProperty("Accept", "application/json")
            }
            connection.connect()

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) {
                Log.d(TAG, "Version endpoint not reachable: $endpointUrl (status: $responseCode)")
                return null
            }

            val jsonStr = connection.inputStream.bufferedReader().use { it.readText() }
            connection.disconnect()

            val root = JSONObject(jsonStr)
            val serverVersionCode = root.optLong("versionCode", 0L)
            val serverVersionName = root.optString("versionName", "")
            val rawApkUrl = root.optString("apkUrl", "")
            val notes = root.optString("notes", "")
            val isForce = root.optBoolean("isForce", false)

            val resolvedApkUrl = if (rawApkUrl.startsWith("http://") || rawApkUrl.startsWith("https://")) {
                rawApkUrl
            } else if (rawApkUrl.isNotBlank()) {
                val origin = URL(endpointUrl)
                val portStr = if (origin.port != -1) ":${origin.port}" else ""
                val prefix = if (rawApkUrl.startsWith("/")) "" else "/"
                "${origin.protocol}://${origin.host}${portStr}${prefix}${rawApkUrl}"
            } else {
                ""
            }

            val currentVersionCode = BuildConfig.VERSION_CODE.toLong()
            Log.d(TAG, "Current versionCode: $currentVersionCode, Server versionCode: $serverVersionCode from $endpointUrl")

            if (serverVersionCode > currentVersionCode && resolvedApkUrl.isNotBlank()) {
                AppUpdateInfo(
                    versionCode = serverVersionCode,
                    versionName = serverVersionName,
                    apkUrl = resolvedApkUrl,
                    notes = notes,
                    isForce = isForce
                )
            } else {
                null
            }
        }.onFailure { e ->
            Log.d(TAG, "Failed to fetch version from $endpointUrl: ${e.message}")
        }.getOrNull()
    }

    /**
     * 알 수 없는 앱 설치 권한 허용 여부를 체크합니다. (Android 8.0 이상)
     */
    fun canInstallPackage(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }

    /**
     * '이 출처의 앱 설치 허용' 설정 화면으로 이동합니다.
     */
    fun openInstallPermissionSetting(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            runCatching {
                val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                    data = Uri.parse("package:${context.packageName}")
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                context.startActivity(intent)
            }.onFailure {
                Log.e(TAG, "Failed to open install permission settings", it)
            }
        }
    }

    /**
     * APK 파일을 스트리밍 방식으로 다운로드하고 진행률(0.0 ~ 1.0)을 콜백으로 알립니다.
     */
    suspend fun downloadApk(
        context: Context,
        apkUrl: String,
        onProgress: (Float) -> Unit
    ): Result<File> = withContext(Dispatchers.IO) {
        runCatching {
            val updateDir = File(context.cacheDir, "updates").apply {
                if (!exists()) mkdirs()
            }
            // 기존 임시 잔여 파일 정리
            updateDir.listFiles()?.forEach { it.delete() }

            val tempFile = File(updateDir, "update_temp.part")
            val targetFile = File(updateDir, "The_Stock_Update.apk")

            val url = URL(apkUrl)
            val connection = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 15000
                readTimeout = 60000
                instanceFollowRedirects = true
                requestMethod = "GET"
            }
            connection.connect()

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) {
                throw IllegalStateException("Server returned HTTP $responseCode")
            }

            val fileLength = connection.contentLength.toLong()
            var inputStream: InputStream? = null
            var outputStream: FileOutputStream? = null

            try {
                inputStream = connection.inputStream
                outputStream = FileOutputStream(tempFile)

                val buffer = ByteArray(8192)
                var total: Long = 0
                var count: Int

                while (inputStream.read(buffer).also { count = it } != -1) {
                    outputStream.write(buffer, 0, count)
                    total += count
                    if (fileLength > 0) {
                        val progress = total.toFloat() / fileLength
                        withContext(Dispatchers.Main) {
                            onProgress(progress.coerceIn(0f, 1f))
                        }
                    } else {
                        withContext(Dispatchers.Main) {
                            onProgress(-1f)
                        }
                    }
                }
                outputStream.flush()
            } finally {
                outputStream?.close()
                inputStream?.close()
                connection.disconnect()
            }

            if (tempFile.exists()) {
                if (targetFile.exists()) targetFile.delete()
                if (!tempFile.renameTo(targetFile)) {
                    tempFile.copyTo(targetFile, overwrite = true)
                    tempFile.delete()
                }
            }

            targetFile
        }
    }

    /**
     * FileProvider를 사용하여 다운로드된 APK 설치 인텐트를 실행합니다.
     */
    fun installApk(context: Context, apkFile: File) {
        runCatching {
            val contentUri: Uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                apkFile
            )

            val installIntent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(contentUri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(installIntent)
        }.onFailure { e ->
            Log.e(TAG, "Failed to launch installer intent: ${e.message}", e)
        }
    }
}
