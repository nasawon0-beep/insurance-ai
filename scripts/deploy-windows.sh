#!/bin/bash
set -e

# 사용법: ./scripts/deploy-windows.sh 2.3.0
# 동작: desktop/Tauri 버전을 갱신하고 MSI를 빌드한 뒤 GitHub Release와 GitHub Pages 업데이트 메타데이터를 갱신합니다.
VERSION=$1

if [ -z "$VERSION" ]; then
  echo "Usage: ./deploy-windows.sh 2.3.0"
  exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must be semantic version format: 2.3.0"
  exit 1
fi

echo "📝 Windows 버전 v${VERSION} 갱신 중..."
cd desktop
npm version "$VERSION" --no-git-tag-version

echo "🔨 Windows v${VERSION} 빌드 중..."
CI=true npm run tauri build

echo "📦 GitHub Release 생성 중..."
cd ..
gh release create v${VERSION}-windows \
  desktop/src-tauri/target/release/bundle/msi/*.msi \
  --title "Windows v${VERSION}" \
  --notes "$(cat CHANGELOG.md 2>/dev/null || echo '- 보장분석 기능 추가')"

echo "📝 업데이트 정보 갱신 중..."
cd ~/insurance-ai-updates

DOWNLOAD_URL="https://github.com/nasawon0-beep/insurance-ai/releases/download/v${VERSION}-windows/insurance-ai_${VERSION}_x64.msi"

cat > api/windows.json <<EOF
{
  "version": "${VERSION}",
  "pub_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "url": "${DOWNLOAD_URL}",
  "signature": "",
  "notes": "보장분석 기능 추가"
}
EOF

git add api/windows.json
git commit -m "Update Windows to v${VERSION}"
git push

echo "✅ 배포 완료!"
echo "🌐 https://nasangwon.github.io/insurance-ai-updates/"
