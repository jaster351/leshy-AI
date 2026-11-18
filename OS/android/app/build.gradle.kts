plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "ru.leshy.satellite"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.leshy.satellite"
        minSdk = 34
        targetSdk = 35
        versionCode = providers.gradleProperty("leshyVersionCode").orElse("1").get().toInt()
        versionName = providers.gradleProperty("leshyVersion").orElse("0.1.0-dev").get()
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.camera:camera-camera2:1.5.2")
    implementation("androidx.camera:camera-lifecycle:1.5.2")
    implementation("androidx.camera:camera-view:1.5.2")
    implementation("com.google.mlkit:face-detection:16.1.7")
}
