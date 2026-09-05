#!/bin/bash
# insurance-ai → GitHub 최초 업로드 + CI 셋업
# 맥 터미널(Terminal.app)에서 실행:  bash /Volumes/Mac_SSD/insurance-ai/push-to-github.sh
set -e

cd /Volumes/Mac_SSD/insurance-ai

echo "==> 1/4  private 저장소 생성 + 업로드"
gh repo create insurance-ai --private --source=. --remote=origin --push

echo "==> 2/4  업데이트 서명키를 CI 비밀값으로 등록"
gh secret set TAURI_SIGNING_PRIVATE_KEY < "$HOME/.insurance-ai-keys/insurance-ai-updater-key.pem" --repo nasawon0-beep/insurance-ai
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body "" --repo nasawon0-beep/insurance-ai

echo "==> 3/4  빌드 워크플로 실행"
gh workflow run "Build desktop app" --repo nasawon0-beep/insurance-ai

echo "==> 4/4  완료. 아래로 진행상황 확인:"
echo "    gh run list --repo nasawon0-beep/insurance-ai"
