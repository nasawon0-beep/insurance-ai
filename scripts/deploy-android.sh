#!/bin/bash
set -e

# 사용법: ./scripts/deploy-android.sh 1.1.0
# 동작: Android Gradle 버전을 갱신하고 APK를 빌드한 뒤 GitHub Release와 GitHub Pages 업데이트 메타데이터를 갱신합니다.
VERSION=$1

if [ -z "$VERSION" ]; then
  echo "Usage: ./deploy-android.sh 1.1.0"
  exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must be semantic version format: 1.1.0"
  exit 1
fi

VERSION_CODE=$(python3 - "$VERSION" <<'PY'
import sys
major, minor, patch = (int(part) for part in sys.argv[1].split('.'))
print(major * 10000 + minor * 100 + patch)
PY
)

echo "📝 Android 버전 v${VERSION} 갱신 중..."
python3 - "$VERSION" "$VERSION_CODE" <<'PY'
import re
import sys
from pathlib import Path

version, version_code = sys.argv[1], sys.argv[2]
path = Path("android/app/build.gradle.kts")
text = path.read_text()
text, code_count = re.subn(r'versionCode = \d+', f'versionCode = {version_code}', text, count=1)
text, name_count = re.subn(r'versionName = "[^"]+"', f'versionName = "{version}"', text, count=1)
if code_count != 1 or name_count != 1:
    raise SystemExit("Failed to update Android version fields")
path.write_text(text)
PY

echo "🔨 Android v${VERSION} 빌드 중..."
cd android
./gradlew assembleRelease

echo "📦 GitHub Release 생성 중..."
cd ..
gh release create v${VERSION}-android \
  android/app/build/outputs/apk/release/*.apk \
  --title "Android v${VERSION}" \
  --notes "$(cat CHANGELOG.md 2>/dev/null || echo '- 고객 관리 기능 추가')"

echo "📝 업데이트 정보 갱신 중..."
cd ~/insurance-ai-updates

DOWNLOAD_URL="https://github.com/nasawon0-beep/insurance-ai/releases/download/v${VERSION}-android/app-release.apk"

cat > api/android.json <<EOF
{
  "latest_version": "${VERSION}",
  "download_url": "${DOWNLOAD_URL}",
  "playstore_url": "https://play.google.com/store/apps/details?id=com.insurance.ai",
  "changelog": "고객 관리 기능 추가",
  "release_date": "$(date +%Y-%m-%d)"
}
EOF

git add api/android.json
git commit -m "Update Android to v${VERSION}"
git push

echo "✅ 배포 완료!"
