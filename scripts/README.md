# 배포 스크립트 사용법

Windows와 Android 릴리스를 빌드하고, GitHub Release 및 GitHub Pages 업데이트 메타데이터를 자동으로 갱신하는 스크립트입니다.

## 사전 준비

- GitHub CLI(`gh`) 로그인 완료
- `~/insurance-ai-updates` 저장소 클론 완료
- Windows 배포: Windows 빌드 환경에서 Tauri/MSI 빌드 가능해야 함
- Android 배포: Android SDK와 Gradle 릴리스 빌드 환경 준비 완료

## Windows 배포

```bash
./scripts/deploy-windows.sh 2.3.0
```

동작 순서:

1. `desktop/package.json` 버전을 입력 버전으로 갱신합니다.
2. `desktop`의 npm `version` 훅으로 Tauri 설정과 Cargo 버전을 동기화합니다.
3. `CI=true npm run tauri build`로 MSI를 빌드합니다.
4. `v{VERSION}-windows` GitHub Release를 생성하고 MSI를 업로드합니다.
5. `~/insurance-ai-updates/api/windows.json`을 갱신한 뒤 커밋/푸시합니다.

## Android 배포

```bash
./scripts/deploy-android.sh 1.1.0
```

동작 순서:

1. `android/app/build.gradle.kts`의 `versionName`과 `versionCode`를 입력 버전으로 갱신합니다.
2. `./gradlew assembleRelease`로 APK를 빌드합니다.
3. `v{VERSION}-android` GitHub Release를 생성하고 APK를 업로드합니다.
4. `~/insurance-ai-updates/api/android.json`을 갱신한 뒤 커밋/푸시합니다.

## 참고

- 버전은 `major.minor.patch` 형식만 허용합니다.
- GitHub Pages 메타데이터는 `~/insurance-ai-updates/api/*.json` 파일에 기록됩니다.
- 실제 배포 전에 작업 트리 변경 사항과 생성된 릴리스 파일명을 확인하세요.
