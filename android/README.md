# Insurance AI Android Phase 1 MVP

로컬 전용 Android MVP입니다. Kotlin + Jetpack Compose, Room + SQLCipher, BiometricPrompt, AndroidKeyStore 기반 필드 암호화를 사용합니다.

## 빌드

```bash
cd android
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export ANDROID_SDK_ROOT="$ANDROID_HOME"
./gradlew testDebugUnitTest assembleDebug assembleDebugAndroidTest
```

디버그 APK: `app/build/outputs/apk/debug/app-debug.apk`

## 범위

- Phase 1 로컬 전용: 네트워크/클라우드 동기화 없음
- SQLCipher로 Room DB 암호화
- 주민번호/주소/메모/상담 내용 AES-256-GCM 필드 암호화
- 생체 인증 또는 기기 잠금 인증으로 앱 진입
