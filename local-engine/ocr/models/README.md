# OCR 모델 (번들 필수 — 다운로드 아님)

Windows/Linux 에서 스캔·이미지 PDF 의 **한글** OCR 에 쓰인다.
RapidOCR 기본 번들은 중국어+영어 인식 모델뿐이라 한글이 한자로 깨진다.

- `korean_PP-OCRv4_rec_mobile.onnx` — PaddleOCR 한국어 인식 모델(ONNX). 출처: ModelScope `RapidAI/RapidOCR` (`onnx/PP-OCRv4/rec/`)
- `korean_dict.txt` — PaddleOCR `ppocr/utils/dict/korean_dict.txt`

`database/router.py` `_rapidocr_engine()` 가 이 두 파일이 있으면 자동으로 로드한다.
없으면 기본 ch/en 모델로 폴백(숫자·영문만 그럭저럭, 한글 부정확).
macOS 는 Vision OCR 을 먼저 쓰므로 이 파일들은 주로 Windows 용.
