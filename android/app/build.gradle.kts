plugins { id("com.android.application") }

android {
    namespace = "cloud.hyungwon.aicompany"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "cloud.hyungwon.aicompany"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        manifestPlaceholders["siteHost"] = "hyungwon.cloud"
        manifestPlaceholders["launchUrl"] = "https://hyungwon.cloud/"
        resValue("string", "launch_url", "https://hyungwon.cloud/")
        resValue("string", "asset_statements", """[{\"relation\":[\"delegate_permission/common.handle_all_urls\"],\"target\":{\"namespace\":\"web\",\"site\":\"https://hyungwon.cloud\"}}]""")
    }

    // CI has no signing material. The verified release APK is signed locally.
    buildTypes { release { isMinifyEnabled = false; isDebuggable = false } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint { abortOnError = true; checkReleaseBuilds = true }
}

dependencies {
    implementation("com.google.androidbrowserhelper:androidbrowserhelper:2.7.3")
    implementation("androidx.browser:browser:1.9.0")
}
