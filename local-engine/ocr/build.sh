#!/bin/sh
# macOS 전용. Vision OCR 헬퍼(pdf-ocr) 빌드.
# 엔진은 database/router.py 에서 ../ocr/pdf-ocr 가 있으면 자동으로 PDF OCR 폴백을 쓴다.
set -e
cd "$(dirname "$0")"

if [ "$(uname)" != "Darwin" ]; then
  echo "pdf-ocr 는 macOS 전용입니다 (Vision 프레임워크). 이 플랫폼에서는 건너뜁니다." >&2
  exit 0
fi

swiftc -O pdf_ocr.swift -o pdf-ocr
echo "빌드 완료: $(pwd)/pdf-ocr"
