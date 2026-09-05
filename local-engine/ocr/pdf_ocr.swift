// 글자 데이터가 없는 PDF(스캔본 / 벡터 외곽선으로 그려진 보험사 출력물)를
// macOS 내장 OCR(Vision)로 읽는다. 다운로드·네트워크 없이 완전 오프라인.
//
//   pdf-ocr <pdf경로> [최대페이지수]
//   → stdout 에 JSON: {"engine":"macos-vision","page_count":N,"pages":[{"page":1,"text":"..."}]}
//
// 빌드: swiftc -O pdf_ocr.swift -o pdf-ocr   (build.sh 참고)

import Foundation
import Vision
import CoreGraphics
import PDFKit

func fail(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: pdf-ocr <pdf> [maxPages]", 2) }

let pdfURL = URL(fileURLWithPath: args[1])
let maxPages = args.count >= 3 ? max(1, Int(args[2]) ?? 15) : 15
let scale: CGFloat = 3.0  // 렌더 배율. 작은 글씨 인식률을 위해 3x.

guard let doc = PDFDocument(url: pdfURL) else { fail("cannot open pdf") }

func ocr(_ cg: CGImage) -> String {
    var lines: [String] = []
    let req = VNRecognizeTextRequest { request, _ in
        guard let obs = request.results as? [VNRecognizedTextObservation] else { return }
        for o in obs {
            if let c = o.topCandidates(1).first { lines.append(c.string) }
        }
    }
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ko-KR", "en-US"]
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    return lines.joined(separator: "\n")
}

func render(_ page: PDFPage) -> CGImage? {
    let rect = page.bounds(for: .mediaBox)
    let w = Int((rect.width * scale).rounded()), h = Int((rect.height * scale).rounded())
    guard w > 0, h > 0,
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.scaleBy(x: scale, y: scale)
    ctx.translateBy(x: -rect.origin.x, y: -rect.origin.y)
    page.draw(with: .mediaBox, to: ctx)
    return ctx.makeImage()
}

var pages: [[String: Any]] = []
let n = min(doc.pageCount, maxPages)
for i in 0..<n {
    guard let page = doc.page(at: i), let cg = render(page) else { continue }
    let text = ocr(cg)
    if !text.isEmpty { pages.append(["page": i + 1, "text": text]) }
}

let payload: [String: Any] = [
    "engine": "macos-vision",
    "page_count": doc.pageCount,
    "pages_ocred": n,
    "pages": pages,
]
let data = try JSONSerialization.data(withJSONObject: payload, options: [])
FileHandle.standardOutput.write(data)
