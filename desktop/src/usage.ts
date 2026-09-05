// 사용 로그 전송 헬퍼. 엔진 POST /usage 로 배치 전송.
// - 실패는 전부 삼킨다 (사용 흐름을 절대 막지 않는다).
// - app_open 은 세션당 1회만.
// - 자유 텍스트/이름/내용은 넣지 말 것 (엔진이 화이트리스트로 한 번 더 거른다).

import { deviceId } from "./auth";
import { engineFetch } from "./engine";
const APP_VERSION = "0.1.0";

type Ev = { event: string; props: Record<string, unknown> };

let queue: Ev[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
const sentOnce = new Set<string>();

export function track(event: string, props: Record<string, unknown> = {}): void {
  try {
    if (event === "app_open") {
      if (sentOnce.has("app_open")) return;
      sentOnce.add("app_open");
    }
    queue.push({ event, props: props || {} });
    if (!timer) timer = setTimeout(flush, 2500);
  } catch {
    /* noop */
  }
}

async function flush(): Promise<void> {
  timer = null;
  const batch = queue;
  queue = [];
  for (const it of batch) {
    try {
      await engineFetch("/usage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event: it.event,
          props: it.props,
          device_id: safeDeviceId(),
          app_version: APP_VERSION,
        }),
      });
    } catch {
      /* 삼킴 */
    }
  }
}

function safeDeviceId(): string | undefined {
  try {
    return deviceId();
  } catch {
    return undefined;
  }
}
